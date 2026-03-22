package com.docintel.document.config

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
import org.springframework.data.redis.stream.StreamMessageListenerContainer
import org.springframework.data.redis.stream.StreamMessageListenerContainer.StreamMessageListenerContainerOptions
import org.springframework.stereotype.Component
import java.time.Duration

@Configuration
class RedisStreamConfig {

    @Bean
    fun streamMessageListenerContainer(
        redisConnectionFactory: RedisConnectionFactory
    ): StreamMessageListenerContainer<String, MapRecord<String, String, String>> {
        val options: StreamMessageListenerContainerOptions<String, MapRecord<String, String, String>> =
            StreamMessageListenerContainerOptions.builder()
                .pollTimeout(Duration.ofSeconds(2))
                .build()
        return StreamMessageListenerContainer.create(redisConnectionFactory, options)
    }
}

/**
 * Wires consumers into the [StreamMessageListenerContainer] once the application
 * context is fully initialised. Ensures consumer groups exist (idempotent MKSTREAM)
 * before starting any listeners.
 *
 * Disabled when `messaging.streams.enabled=false` (e.g. in unit tests).
 */
@Component
@ConditionalOnProperty(name = ["messaging.streams.enabled"], havingValue = "true", matchIfMissing = true)
class StreamConsumerStarter(
    private val container: StreamMessageListenerContainer<String, MapRecord<String, String, String>>,
    private val filesAvailableConsumer: FilesAvailableConsumer,
    private val ingestionCompleteConsumer: IngestionCompleteConsumer,
    private val redisTemplate: StringRedisTemplate,
    @Value("\${messaging.streams.consumer-name:document-service-1}") private val consumerName: String
) {
    private val logger = LoggerFactory.getLogger(StreamConsumerStarter::class.java)

    @EventListener(ApplicationReadyEvent::class)
    fun start() {
        ensureGroup(StreamTopics.FILES_AVAILABLE, FilesAvailableConsumer.CONSUMER_GROUP)
        ensureGroup(StreamTopics.INGESTION_COMPLETE, IngestionCompleteConsumer.CONSUMER_GROUP)

        container.receive(
            Consumer.from(FilesAvailableConsumer.CONSUMER_GROUP, consumerName),
            StreamOffset.create(StreamTopics.FILES_AVAILABLE, ReadOffset.lastConsumed()),
            filesAvailableConsumer
        )
        container.receive(
            Consumer.from(IngestionCompleteConsumer.CONSUMER_GROUP, consumerName),
            StreamOffset.create(StreamTopics.INGESTION_COMPLETE, ReadOffset.lastConsumed()),
            ingestionCompleteConsumer
        )

        container.start()
        logger.info(
            "Redis Stream consumers started (consumer='{}') — listening on: {}, {}",
            consumerName, StreamTopics.FILES_AVAILABLE, StreamTopics.INGESTION_COMPLETE
        )
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
            if (e.message?.contains("BUSYGROUP") == true) {
                logger.debug("Consumer group '{}' on stream '{}' already exists", group, stream)
            } else {
                logger.warn("Could not create group '{}' on '{}': {}", group, stream, e.message)
            }
        }
    }
}
