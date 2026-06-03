package com.docintel.document.config

import com.docintel.document.messaging.DocumentProgressConsumer
import com.docintel.document.messaging.FilesAvailableConsumer
import com.docintel.document.messaging.IngestionCompleteConsumer
import com.docintel.document.messaging.StreamTopics
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.boot.context.event.ApplicationReadyEvent
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.event.EventListener
import org.springframework.data.redis.connection.RedisConnectionFactory
import org.springframework.data.redis.connection.stream.Consumer
import org.springframework.data.redis.connection.stream.MapRecord
import org.springframework.data.redis.connection.stream.ReadOffset
import org.springframework.data.redis.connection.stream.StreamOffset
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.stream.StreamListener
import org.springframework.data.redis.stream.StreamMessageListenerContainer
import org.springframework.data.redis.stream.StreamMessageListenerContainer.StreamMessageListenerContainerOptions
import org.springframework.data.redis.stream.Subscription
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import org.springframework.util.ErrorHandler
import java.time.Duration
import java.util.concurrent.ConcurrentHashMap

@Configuration
class RedisStreamConfig {

    private val logger = LoggerFactory.getLogger(RedisStreamConfig::class.java)

    @Bean
    fun streamMessageListenerContainer(
        redisConnectionFactory: RedisConnectionFactory
    ): StreamMessageListenerContainer<String, MapRecord<String, String, String>> {
        // Custom error handler keeps logs tidy (one line per error) and lets the
        // periodic health check (StreamConsumerStarter.healthCheck) recreate any
        // subscription Spring tears down on fatal errors like RedisCommandTimeoutException
        // (typically caused by laptop sleep/wake or a Redis blip).
        val errorHandler = ErrorHandler { throwable ->
            logger.warn("Redis stream consumer error (subscription will be recreated): {}", throwable.message)
        }

        val options: StreamMessageListenerContainerOptions<String, MapRecord<String, String, String>> =
            StreamMessageListenerContainerOptions.builder()
                .pollTimeout(Duration.ofSeconds(2))
                .errorHandler(errorHandler)
                .build()
        return StreamMessageListenerContainer.create(redisConnectionFactory, options)
    }
}

/**
 * Wires consumers into the [StreamMessageListenerContainer] once the application
 * context is fully initialised. Ensures consumer groups exist (idempotent MKSTREAM)
 * before starting any listeners.
 *
 * Subscriptions are tracked and a periodic health check recreates any that
 * become inactive — this handles fatal errors like RedisCommandTimeoutException
 * (caused by laptop sleep/wake, brief Redis unavailability, etc.) which would
 * otherwise leave the consumer silently dead.
 *
 * Disabled when `messaging.streams.enabled=false` (e.g. in unit tests).
 */
@Component
@ConditionalOnProperty(name = ["messaging.streams.enabled"], havingValue = "true", matchIfMissing = true)
class StreamConsumerStarter(
    private val container: StreamMessageListenerContainer<String, MapRecord<String, String, String>>,
    private val filesAvailableConsumer: FilesAvailableConsumer,
    private val ingestionCompleteConsumer: IngestionCompleteConsumer,
    private val documentProgressConsumer: DocumentProgressConsumer,
    private val redisTemplate: StringRedisTemplate,
    @Value("\${messaging.streams.consumer-name:document-service-1}") private val consumerName: String
) {
    private val logger = LoggerFactory.getLogger(StreamConsumerStarter::class.java)

    // Track subscriptions so the health check can detect dead ones and recreate them.
    private val subscriptions = ConcurrentHashMap<String, Subscription>()

    private data class StreamBinding(
        val key: String,
        val stream: String,
        val group: String,
        val listener: StreamListener<String, MapRecord<String, String, String>>,
    )

    private fun bindings(): List<StreamBinding> = listOf(
        StreamBinding(
            "files_available", StreamTopics.FILES_AVAILABLE,
            FilesAvailableConsumer.CONSUMER_GROUP, filesAvailableConsumer,
        ),
        StreamBinding(
            "ingestion_complete", StreamTopics.INGESTION_COMPLETE,
            IngestionCompleteConsumer.CONSUMER_GROUP, ingestionCompleteConsumer,
        ),
        StreamBinding(
            "documents_progress", StreamTopics.DOCUMENTS_PROGRESS,
            DocumentProgressConsumer.CONSUMER_GROUP, documentProgressConsumer,
        ),
    )

    @EventListener(ApplicationReadyEvent::class)
    fun start() {
        bindings().forEach { ensureGroup(it.stream, it.group) }
        bindings().forEach { subscribe(it) }
        container.start()
        logger.info(
            "Redis Stream consumers started (consumer='{}') — listening on: {}",
            consumerName, bindings().joinToString(", ") { it.stream },
        )
    }

    /**
     * Periodic liveness check for stream subscriptions. Spring's
     * `StreamMessageListenerContainer` cancels a subscription on certain errors
     * (notably `RedisCommandTimeoutException` after laptop sleep/wake) without
     * automatically recreating it — leaving the consumer silently dead. This
     * sweeper detects inactive subscriptions and re-subscribes them.
     *
     * Runs every 30s.
     */
    @Scheduled(fixedDelay = 30_000, initialDelay = 30_000)
    fun healthCheck() {
        bindings().forEach { binding ->
            val sub = subscriptions[binding.key]
            if (sub == null || !sub.isActive) {
                logger.warn(
                    "Stream subscription '{}' (stream={}) is inactive — recreating",
                    binding.key, binding.stream,
                )
                runCatching { sub?.cancel() }
                subscribe(binding)
            }
        }
    }

    private fun subscribe(binding: StreamBinding) {
        try {
            val subscription = container.receive(
                Consumer.from(binding.group, consumerName),
                StreamOffset.create(binding.stream, ReadOffset.lastConsumed()),
                binding.listener,
            )
            subscriptions[binding.key] = subscription
            logger.info("Subscribed to stream '{}' as group '{}'", binding.stream, binding.group)
        } catch (e: Exception) {
            logger.error("Failed to subscribe to stream '{}': {}", binding.stream, e.message, e)
        }
    }

    /**
     * Create the consumer group if it does not exist.
     * Uses raw XGROUP CREATE … MKSTREAM so the stream is also created if absent.
     * Spring's StreamOperations.createGroup(3-arg) does not pass MKSTREAM to Redis,
     * so we go through the Lettuce connection directly.
     */
    private fun ensureGroup(stream: String, group: String) {
        try {
            redisTemplate.execute { connection ->
                connection.streamCommands()
                    .xGroupCreate(stream.toByteArray(Charsets.UTF_8), group, ReadOffset.latest(), true)
            }
            logger.info("Created consumer group '{}' on stream '{}'", group, stream)
        } catch (e: Exception) {
            // Lettuce wraps BUSYGROUP in a generic "Error in execution" — also check the cause chain.
            val msg = generateSequence<Throwable>(e) { it.cause }
                .mapNotNull { it.message }
                .joinToString(" | ")
            if (msg.contains("BUSYGROUP")) {
                logger.debug("Consumer group '{}' on stream '{}' already exists", group, stream)
            } else {
                logger.warn("Could not create group '{}' on '{}': {}", group, stream, msg)
            }
        }
    }
}
