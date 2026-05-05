package com.docintel.document.messaging

import com.docintel.document.sse.DocumentStatusEvent
import com.docintel.document.sse.ProgressPayload
import com.fasterxml.jackson.databind.ObjectMapper
import org.slf4j.LoggerFactory
import org.springframework.context.ApplicationEventPublisher
import org.springframework.data.redis.connection.stream.MapRecord
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.stream.StreamListener
import org.springframework.stereotype.Component

/**
 * Consumes [StreamTopics.DOCUMENTS_PROGRESS] stream events published by the
 * ingestion-service after each PDF page shard completes.
 *
 * Translates into a [DocumentStatusEvent] with a [ProgressPayload] so the UI
 * can display "Page 47/250 • Converting" instead of a static spinner.
 *
 * The document status remains PROCESSING throughout shard processing — only
 * [IngestionCompleteConsumer] transitions to COMPLETED / FAILED.
 */
@Component
class DocumentProgressConsumer(
    private val eventPublisher: ApplicationEventPublisher,
    private val redisTemplate: StringRedisTemplate,
    private val objectMapper: ObjectMapper,
) : StreamListener<String, MapRecord<String, String, String>> {

    private val logger = LoggerFactory.getLogger(DocumentProgressConsumer::class.java)

    companion object {
        const val CONSUMER_GROUP = "document-service"
    }

    override fun onMessage(message: MapRecord<String, String, String>) {
        val raw = message.value["payload"]
        if (raw.isNullOrBlank()) {
            ack(message)
            return
        }

        try {
            val node = objectMapper.readTree(raw)
            val documentId  = node.path("documentId").asText("")
            val tenantId    = node.path("tenantId").asText("")
            val currentPage = node.path("currentPage").asInt(0)
            val totalPages  = node.path("totalPages").asInt(0)
            val stage       = node.path("stage").asText("Processing")
            val filename    = node.path("filename").asText("")

            if (documentId.isBlank() || tenantId.isBlank()) {
                logger.warn("documents.progress event missing documentId/tenantId: id={}", message.id)
                ack(message)
                return
            }

            eventPublisher.publishEvent(
                DocumentStatusEvent(
                    documentId = documentId,
                    tenantId   = tenantId,
                    status     = "PROCESSING",
                    stage      = stage,
                    filename   = filename,
                    progress   = ProgressPayload(
                        currentPage  = currentPage,
                        totalPages   = totalPages,
                        currentStage = stage,
                    ),
                )
            )
        } catch (e: Exception) {
            logger.error("Failed to process documents.progress id={}: {}", message.id, e.message)
        }

        ack(message)
    }

    private fun ack(message: MapRecord<String, String, String>) {
        redisTemplate.opsForStream<String, String>()
            .acknowledge(StreamTopics.DOCUMENTS_PROGRESS, CONSUMER_GROUP, message.id)
    }
}
