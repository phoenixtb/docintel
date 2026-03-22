package com.docintel.document.messaging

import com.docintel.document.dto.DocumentResponse
import com.docintel.document.dto.FromPathRequest
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.service.DocumentService
import com.fasterxml.jackson.module.kotlin.jacksonObjectMapper
import io.mockk.*
import io.mockk.impl.annotations.MockK
import io.mockk.junit5.MockKExtension
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.extension.ExtendWith
import org.springframework.data.redis.connection.stream.MapRecord
import org.springframework.data.redis.connection.stream.RecordId
import org.springframework.data.redis.core.StreamOperations
import org.springframework.data.redis.core.StringRedisTemplate
import java.time.Instant
import java.util.UUID
import kotlin.test.assertEquals

/**
 * Unit tests for stream consumers using MockK — no Spring context required.
 *
 * Tests cover:
 *  - FilesAvailableConsumer: new document, dedup, malformed payload, service error (no ack)
 *  - IngestionCompleteConsumer: COMPLETED, FAILED status, malformed payload, service error
 */
@ExtendWith(MockKExtension::class)
class StreamConsumerTest {

    @MockK private lateinit var documentService: DocumentService
    @MockK private lateinit var streamPublisher: DocumentStreamPublisher
    @MockK private lateinit var redisTemplate: StringRedisTemplate
    @MockK private lateinit var streamOps: StreamOperations<String, String, String>

    private val objectMapper = jacksonObjectMapper()

    private lateinit var filesConsumer: FilesAvailableConsumer
    private lateinit var ingestionConsumer: IngestionCompleteConsumer

    @BeforeEach
    fun setUp() {
        every { redisTemplate.opsForStream<String, String>() } returns streamOps
        every { streamOps.acknowledge(any(), any(), any<RecordId>()) } returns 1L

        filesConsumer = FilesAvailableConsumer(
            documentService = documentService,
            streamPublisher = streamPublisher,
            redisTemplate = redisTemplate,
            objectMapper = objectMapper
        )
        ingestionConsumer = IngestionCompleteConsumer(
            documentService = documentService,
            redisTemplate = redisTemplate,
            objectMapper = objectMapper
        )
    }

    // =========================================================================
    // FilesAvailableConsumer
    // =========================================================================

    @Test
    fun `FilesAvailableConsumer should register new document and publish DocumentReady`() {
        val docId = UUID.randomUUID()
        val event = FilesAvailableEvent(
            minioPath    = "docs/abc/original.txt",
            contentHash  = "a".repeat(64),
            tenantId     = "test-tenant",
            filename     = "test.txt",
            contentType  = "text/plain",
            fileSize     = 100L
        )
        val message = mockMessage(StreamTopics.FILES_AVAILABLE, event)
        val docResponse = docResponse(docId)

        every { documentService.registerFromPath(any(), any()) } returns Pair(docResponse, false)
        every { streamPublisher.publishDocumentReady(any()) } returns mockk()

        filesConsumer.onMessage(message)

        verify { documentService.registerFromPath(any<FromPathRequest>(), "test-tenant") }
        verify { streamPublisher.publishDocumentReady(any()) }
        verify { streamOps.acknowledge(StreamTopics.FILES_AVAILABLE, FilesAvailableConsumer.CONSUMER_GROUP, any<RecordId>()) }
    }

    @Test
    fun `FilesAvailableConsumer should skip publishDocumentReady on dedup hit`() {
        val docId = UUID.randomUUID()
        val event = FilesAvailableEvent(
            minioPath   = "docs/dup/original.txt",
            contentHash = "b".repeat(64),
            tenantId    = "tenant-x",
            filename    = "dup.txt"
        )
        val message = mockMessage(StreamTopics.FILES_AVAILABLE, event)

        every { documentService.registerFromPath(any(), any()) } returns Pair(docResponse(docId), true)

        filesConsumer.onMessage(message)

        verify(exactly = 0) { streamPublisher.publishDocumentReady(any()) }
        verify { streamOps.acknowledge(StreamTopics.FILES_AVAILABLE, FilesAvailableConsumer.CONSUMER_GROUP, any<RecordId>()) }
    }

    @Test
    fun `FilesAvailableConsumer should ack and skip processing when payload is missing`() {
        val message: MapRecord<String, String, String> = mockk()
        every { message.value } returns emptyMap()
        every { message.id } returns RecordId.of("1-1")

        filesConsumer.onMessage(message)

        verify(exactly = 0) { documentService.registerFromPath(any(), any()) }
        verify { streamOps.acknowledge(StreamTopics.FILES_AVAILABLE, FilesAvailableConsumer.CONSUMER_GROUP, any<RecordId>()) }
    }

    @Test
    fun `FilesAvailableConsumer should ack and skip on malformed JSON payload`() {
        val message = mockRawMessage(StreamTopics.FILES_AVAILABLE, "{not-valid-json}")

        filesConsumer.onMessage(message)

        verify(exactly = 0) { documentService.registerFromPath(any(), any()) }
        verify { streamOps.acknowledge(StreamTopics.FILES_AVAILABLE, FilesAvailableConsumer.CONSUMER_GROUP, any<RecordId>()) }
    }

    @Test
    fun `FilesAvailableConsumer should NOT ack when registerFromPath throws`() {
        val event = FilesAvailableEvent(
            minioPath   = "docs/err/original.txt",
            contentHash = "c".repeat(64),
            tenantId    = "tenant-err",
            filename    = "err.txt"
        )
        val message = mockMessage(StreamTopics.FILES_AVAILABLE, event)

        every { documentService.registerFromPath(any(), any()) } throws RuntimeException("DB down")

        filesConsumer.onMessage(message)

        verify(exactly = 0) { streamOps.acknowledge(any(), any(), any<RecordId>()) }
    }

    // =========================================================================
    // IngestionCompleteConsumer
    // =========================================================================

    @Test
    fun `IngestionCompleteConsumer should call markDocumentCompleted on COMPLETED status`() {
        val docId = UUID.randomUUID()
        val event = IngestionCompleteEvent(
            documentId  = docId.toString(),
            tenantId    = "tenant-a",
            chunkCount  = 15,
            domain      = "contracts",
            status      = "COMPLETED"
        )
        val message = mockMessage(StreamTopics.INGESTION_COMPLETE, event)

        every { documentService.markDocumentCompleted(docId, "tenant-a", 15) } just Runs

        ingestionConsumer.onMessage(message)

        verify { documentService.markDocumentCompleted(docId, "tenant-a", 15) }
        verify { streamOps.acknowledge(StreamTopics.INGESTION_COMPLETE, IngestionCompleteConsumer.CONSUMER_GROUP, any<RecordId>()) }
    }

    @Test
    fun `IngestionCompleteConsumer should call markDocumentFailed on FAILED status`() {
        val docId = UUID.randomUUID()
        val event = IngestionCompleteEvent(
            documentId   = docId.toString(),
            tenantId     = "tenant-b",
            chunkCount   = 0,
            domain       = "general",
            status       = "FAILED",
            errorMessage = "pipeline error"
        )
        val message = mockMessage(StreamTopics.INGESTION_COMPLETE, event)

        every { documentService.markDocumentFailed(docId, "tenant-b", "pipeline error") } just Runs

        ingestionConsumer.onMessage(message)

        verify { documentService.markDocumentFailed(docId, "tenant-b", "pipeline error") }
        verify { streamOps.acknowledge(StreamTopics.INGESTION_COMPLETE, IngestionCompleteConsumer.CONSUMER_GROUP, any<RecordId>()) }
    }

    @Test
    fun `IngestionCompleteConsumer should ack on malformed payload`() {
        val message = mockRawMessage(StreamTopics.INGESTION_COMPLETE, "{{bad")

        ingestionConsumer.onMessage(message)

        verify(exactly = 0) { documentService.markDocumentCompleted(any(), any(), any()) }
        verify { streamOps.acknowledge(StreamTopics.INGESTION_COMPLETE, IngestionCompleteConsumer.CONSUMER_GROUP, any<RecordId>()) }
    }

    @Test
    fun `IngestionCompleteConsumer should NOT ack when markDocumentCompleted throws`() {
        val docId = UUID.randomUUID()
        val event = IngestionCompleteEvent(
            documentId = docId.toString(),
            tenantId   = "tenant-err",
            chunkCount = 5,
            domain     = "general",
            status     = "COMPLETED"
        )
        val message = mockMessage(StreamTopics.INGESTION_COMPLETE, event)

        every { documentService.markDocumentCompleted(any(), any(), any()) } throws RuntimeException("tx error")

        ingestionConsumer.onMessage(message)

        verify(exactly = 0) { streamOps.acknowledge(any(), any(), any<RecordId>()) }
    }

    // =========================================================================
    // Helpers
    // =========================================================================

    private fun <T : Any> mockMessage(stream: String, event: T): MapRecord<String, String, String> {
        val record: MapRecord<String, String, String> = mockk()
        every { record.value } returns mapOf("payload" to objectMapper.writeValueAsString(event))
        every { record.id } returns RecordId.of("1-1")
        every { record.stream } returns stream
        return record
    }

    private fun mockRawMessage(stream: String, raw: String): MapRecord<String, String, String> {
        val record: MapRecord<String, String, String> = mockk()
        every { record.value } returns mapOf("payload" to raw)
        every { record.id } returns RecordId.of("1-2")
        every { record.stream } returns stream
        return record
    }

    private fun docResponse(id: UUID) = DocumentResponse(
        id          = id,
        filename    = "test.txt",
        contentType = "text/plain",
        fileSize    = 100L,
        chunkCount  = 0,
        status      = ProcessingStatus.PENDING,
        metadata    = emptyMap(),
        createdAt   = Instant.now(),
        updatedAt   = Instant.now()
    )
}
