package com.docintel.document.messaging

import com.fasterxml.jackson.databind.ObjectMapper
import org.slf4j.LoggerFactory
import org.springframework.data.redis.connection.stream.RecordId
import org.springframework.data.redis.connection.stream.StreamRecords
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.stereotype.Component

/**
 * Publishes events to Redis Streams on behalf of document-service.
 *
 * Currently publishes:
 *   - [StreamTopics.DOCUMENTS_READY] after a document record is persisted and
 *     ready for ingestion (triggered from the [FilesAvailableConsumer]).
 */
@Component
class DocumentStreamPublisher(
    private val redisTemplate: StringRedisTemplate,
    private val objectMapper: ObjectMapper
) {
    private val logger = LoggerFactory.getLogger(DocumentStreamPublisher::class.java)

    fun publishDocumentReady(event: DocumentReadyEvent): RecordId {
        val payload = objectMapper.writeValueAsString(event)
        val record = StreamRecords.newRecord()
            .`in`(StreamTopics.DOCUMENTS_READY)
            .ofMap(mapOf("payload" to payload))
        val id = redisTemplate.opsForStream<String, String>().add(record)!!
        logger.debug(
            "Published documents.ready: document_id={} tenant={} id={}",
            event.documentId, event.tenantId, id
        )
        return id
    }
}
