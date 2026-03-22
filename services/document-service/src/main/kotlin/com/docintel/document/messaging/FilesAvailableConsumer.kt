package com.docintel.document.messaging

import com.docintel.document.dto.FromPathRequest
import com.docintel.document.service.DocumentService
import com.docintel.document.sse.DocumentStatusEvent
import com.docintel.document.tenant.TenantContext
import com.docintel.document.tenant.TenantContextHolder
import com.fasterxml.jackson.databind.ObjectMapper
import org.slf4j.LoggerFactory
import org.springframework.context.ApplicationEventPublisher
import org.springframework.data.redis.connection.stream.MapRecord
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.data.redis.stream.StreamListener
import org.springframework.stereotype.Component
import java.util.UUID

/**
 * Consumes the [StreamTopics.FILES_AVAILABLE] stream.
 *
 * Produced by the data-loader service after a file is uploaded to MinIO.
 * For each message:
 *   1. Register the document record via [DocumentService.registerFromPath]
 *      (dedup-safe — returns existing if content_hash already known).
 *   2. If new (not deduplicated), publish [DocumentReadyEvent] to
 *      [StreamTopics.DOCUMENTS_READY] so ingestion-service picks it up.
 *   3. Acknowledge the message to remove it from the pending-entry list.
 */
@Component
class FilesAvailableConsumer(
    private val documentService: DocumentService,
    private val streamPublisher: DocumentStreamPublisher,
    private val redisTemplate: StringRedisTemplate,
    private val objectMapper: ObjectMapper,
    private val eventPublisher: ApplicationEventPublisher,
) : StreamListener<String, MapRecord<String, String, String>> {

    private val logger = LoggerFactory.getLogger(FilesAvailableConsumer::class.java)

    companion object {
        const val CONSUMER_GROUP = "document-service"
    }

    override fun onMessage(message: MapRecord<String, String, String>) {
        val raw = message.value["payload"]
        if (raw.isNullOrBlank()) {
            logger.warn("Received files.available message with no payload: id={}", message.id)
            ack(message)
            return
        }

        val event = try {
            objectMapper.readValue(raw, FilesAvailableEvent::class.java)
        } catch (e: Exception) {
            logger.error("Malformed files.available payload id={}: {}", message.id, e.message)
            ack(message)
            return
        }

        try {
            // Stream consumers run outside the HTTP filter chain — tenant context must be
            // set explicitly so TenantAwareDataSource can apply the correct RLS session vars.
            TenantContextHolder.set(TenantContext(tenantId = event.tenantId, userRole = "tenant_user", userId = ""))
            val request = FromPathRequest(
                contentHash  = event.contentHash,
                minioPath    = event.minioPath,
                filename     = event.filename,
                contentType  = event.contentType,
                fileSize     = event.fileSize,
                dataSourceId = event.dataSourceId?.let { UUID.fromString(it) },
                metadata     = event.metadata
            )

            val (doc, isDuplicate) = documentService.registerFromPath(request, event.tenantId)

            if (!isDuplicate) {
                eventPublisher.publishEvent(DocumentStatusEvent(
                    documentId = doc.id.toString(),
                    tenantId   = event.tenantId,
                    status     = "PENDING",
                    stage      = "Queued",
                    filename   = event.filename,
                ))
                streamPublisher.publishDocumentReady(
                    DocumentReadyEvent(
                        documentId = doc.id.toString(),
                        tenantId   = event.tenantId,
                        bucket     = "docintel-${event.tenantId}",
                        objectPath = event.minioPath,
                        filename   = event.filename,
                        domainHint = event.domainHint,
                        metadata   = event.metadata
                    )
                )
                logger.info(
                    "Registered + queued for ingestion: document_id={} tenant={} file={}",
                    doc.id, event.tenantId, event.filename
                )
            } else {
                logger.debug(
                    "Dedup hit on files.available: document_id={} tenant={} file={}",
                    doc.id, event.tenantId, event.filename
                )
            }
        } catch (e: Exception) {
            logger.error(
                "Failed to process files.available message id={} file={}: {}",
                message.id, event.filename, e.message, e
            )
            // Intentionally do NOT ack — the message will be redelivered (pending-entry list).
            return
        } finally {
            TenantContextHolder.clear()
        }

        ack(message)
    }

    private fun ack(message: MapRecord<String, String, String>) {
        redisTemplate.opsForStream<String, String>()
            .acknowledge(StreamTopics.FILES_AVAILABLE, CONSUMER_GROUP, message.id)
    }
}
