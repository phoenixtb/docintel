package com.docintel.document.messaging

import com.docintel.document.service.DocumentService
import com.docintel.document.tenant.TenantContext
import com.docintel.document.tenant.TenantContextHolder
import com.fasterxml.jackson.databind.ObjectMapper
import org.slf4j.LoggerFactory
import org.springframework.data.redis.connection.stream.MapRecord
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.stream.StreamListener
import org.springframework.stereotype.Component
import java.util.UUID

/**
 * Consumes the [StreamTopics.INGESTION_COMPLETE] stream.
 *
 * Published by the ingestion-service after the Haystack pipeline completes
 * (or fails) for a given document. Updates the document record in PostgreSQL
 * to its final status (COMPLETED / FAILED) and sets chunk_count.
 */
@Component
class IngestionCompleteConsumer(
    private val documentService: DocumentService,
    private val redisTemplate: StringRedisTemplate,
    private val objectMapper: ObjectMapper,
) : StreamListener<String, MapRecord<String, String, String>> {

    private val logger = LoggerFactory.getLogger(IngestionCompleteConsumer::class.java)

    companion object {
        const val CONSUMER_GROUP = "document-service"
    }

    override fun onMessage(message: MapRecord<String, String, String>) {
        val raw = message.value["payload"]
        if (raw.isNullOrBlank()) {
            logger.warn("Received ingestion.complete message with no payload: id={}", message.id)
            ack(message)
            return
        }

        val event = try {
            objectMapper.readValue(raw, IngestionCompleteEvent::class.java)
        } catch (e: Exception) {
            logger.error("Malformed ingestion.complete payload id={}: {}", message.id, e.message)
            ack(message)
            return
        }

        try {
            TenantContextHolder.set(TenantContext(tenantId = event.tenantId, userRole = "tenant_user", userId = ""))
            val documentId = UUID.fromString(event.documentId)
            if (event.status == "COMPLETED") {
                documentService.markDocumentCompleted(documentId, event.tenantId, event.chunkCount)
                logger.info(
                    "Document marked COMPLETED via stream: document_id={} tenant={} chunks={}",
                    event.documentId, event.tenantId, event.chunkCount
                )
            } else {
                documentService.markDocumentFailed(documentId, event.tenantId, event.errorMessage)
                logger.warn(
                    "Document marked FAILED via stream: document_id={} tenant={} error={}",
                    event.documentId, event.tenantId, event.errorMessage
                )
            }
        } catch (e: Exception) {
            logger.error(
                "Failed to process ingestion.complete message id={} document={}: {}",
                message.id, event.documentId, e.message, e
            )
            return
        } finally {
            TenantContextHolder.clear()
        }

        ack(message)
    }

    private fun ack(message: MapRecord<String, String, String>) {
        redisTemplate.opsForStream<String, String>()
            .acknowledge(StreamTopics.INGESTION_COMPLETE, CONSUMER_GROUP, message.id)
    }
}
