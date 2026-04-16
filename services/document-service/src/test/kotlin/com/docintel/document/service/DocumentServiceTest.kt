package com.docintel.document.service

import com.docintel.document.dto.DataSourceRequest
import com.docintel.document.dto.FromPathRequest
import com.docintel.document.entity.Chunk
import com.docintel.document.entity.DataSource
import com.docintel.document.entity.DataSourceStatus
import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.entity.DeletionTask
import com.docintel.document.repository.ChunkRepository
import com.docintel.document.repository.DataSourceRepository
import com.docintel.document.repository.DeletionTaskRepository
import com.docintel.document.repository.DocumentRepository
import io.mockk.*
import io.mockk.impl.annotations.MockK
import io.mockk.junit5.MockKExtension
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.extension.ExtendWith
import org.springframework.context.ApplicationEventPublisher
import org.springframework.data.domain.PageImpl
import org.springframework.data.domain.Pageable
import org.springframework.mock.web.MockMultipartFile
import java.time.Instant
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Unit tests for DocumentService using MockK.
 */
@ExtendWith(MockKExtension::class)
class DocumentServiceTest {

    @MockK
    private lateinit var documentRepository: DocumentRepository

    @MockK
    private lateinit var dataSourceRepository: DataSourceRepository

    @MockK
    private lateinit var chunkRepository: ChunkRepository

    @MockK
    private lateinit var deletionTaskRepository: DeletionTaskRepository

    @MockK
    private lateinit var storageService: StorageService

    @MockK
    private lateinit var ingestionServiceClient: IngestionServiceClient

    @MockK(relaxed = true)
    private lateinit var eventPublisher: ApplicationEventPublisher

    private lateinit var documentService: DocumentService

    private val testTenantId = "test-tenant"
    private val testDocumentId = UUID.randomUUID()

    @BeforeEach
    fun setUp() {
        documentService = DocumentService(
            documentRepository,
            dataSourceRepository,
            chunkRepository,
            deletionTaskRepository,
            storageService,
            ingestionServiceClient,
            eventPublisher,
        )
    }

    @Test
    fun `should upload document and save to repository`() {
        val file = MockMultipartFile("file", "test-doc.txt", "text/plain", "Test content".toByteArray())

        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null
        every { storageService.storeFile(any(), any(), any()) } returns "docs/abc/original.txt"
        every { documentRepository.save(any()) } answers { firstArg() }

        val (result, deduplicated) = documentService.uploadDocument(file, testTenantId, emptyMap())

        assertNotNull(result)
        assertEquals("test-doc.txt", result.filename)
        assertEquals(ProcessingStatus.PENDING, result.status)
        assertFalse(deduplicated)

        verify { storageService.storeFile(file, testTenantId, any()) }
        verify { documentRepository.save(any()) }
    }

    @Test
    fun `should return deduplicated=true for already COMPLETED document`() {
        val file = MockMultipartFile("file", "test.txt", "text/plain", "Same content".toByteArray())
        val (expectedId, _) = DocumentService.computeContentId(testTenantId, "Same content".toByteArray())
        val existing = createTestDocument(id = expectedId, status = ProcessingStatus.COMPLETED)

        every { documentRepository.findByIdAndTenantId(expectedId, testTenantId) } returns existing

        val (result, deduplicated) = documentService.uploadDocument(file, testTenantId, emptyMap())

        assertTrue(deduplicated)
        assertEquals(expectedId, result.id)
        verify(exactly = 0) { storageService.storeFile(any(), any(), any()) }
    }

    @Test
    fun `should upload document with metadata`() {
        val file = MockMultipartFile("file", "doc.txt", "text/plain", "content".toByteArray())
        val metadata = mapOf("author" to "John", "department" to "HR")

        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null
        every { storageService.storeFile(any(), any(), any()) } returns "docs/abc/original.txt"
        every { documentRepository.save(any()) } answers { firstArg() }

        val (result, _) = documentService.uploadDocument(file, testTenantId, metadata)

        assertEquals("John", result.metadata["author"])
        assertEquals("HR", result.metadata["department"])
    }

    @Test
    fun `should get document by id and tenant`() {
        val document = createTestDocument()

        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        every { chunkRepository.findByDocumentIdOrderByChunkIndex(testDocumentId) } returns emptyList()

        val result = documentService.getDocument(testDocumentId, testTenantId, false)

        assertNotNull(result)
        assertEquals(testDocumentId, result.id)
        assertEquals("test.txt", result.filename)
    }

    @Test
    fun `should return null for non-existent document`() {
        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null

        val result = documentService.getDocument(UUID.randomUUID(), testTenantId, false)

        assertNull(result)
    }

    @Test
    fun `should get document with chunks when requested`() {
        val document = createTestDocument()

        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        every { chunkRepository.findByDocumentIdOrderByChunkIndex(testDocumentId) } returns listOf(
            createTestChunk(0),
            createTestChunk(1)
        )

        val result = documentService.getDocument(testDocumentId, testTenantId, includeChunks = true)

        assertNotNull(result)
        assertNotNull(result.chunks)
        assertEquals(2, result.chunks!!.size)
    }

    @Test
    fun `should list documents for tenant`() {
        val documents = listOf(
            createTestDocument(),
            createTestDocument(id = UUID.randomUUID(), filename = "doc2.txt")
        )
        val page = PageImpl(documents)

        every { documentRepository.findByTenantIdAndStatusNot(testTenantId, ProcessingStatus.DELETING, any()) } returns page

        val result = documentService.listDocuments(testTenantId, null, Pageable.unpaged())

        assertEquals(2, result.totalElements)
    }

    @Test
    fun `should list documents filtered by status`() {
        val documents = listOf(createTestDocument(status = ProcessingStatus.COMPLETED))
        val page = PageImpl(documents)

        every {
            documentRepository.findByTenantIdAndStatus(testTenantId, ProcessingStatus.COMPLETED, any())
        } returns page

        val result = documentService.listDocuments(testTenantId, ProcessingStatus.COMPLETED, Pageable.unpaged())

        assertEquals(1, result.totalElements)
        assertEquals(ProcessingStatus.COMPLETED, result.content[0].status)
    }

    @Test
    fun `should queue document for deletion via markForDeletion`() {
        val document = createTestDocument()

        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        every { documentRepository.save(any()) } answers { firstArg() }
        every { deletionTaskRepository.save(any<DeletionTask>()) } answers { firstArg() }

        val result = documentService.markForDeletion(testDocumentId, testTenantId)

        assertTrue(result)
        verify { documentRepository.save(match { it.status == ProcessingStatus.DELETING }) }
        verify { deletionTaskRepository.save(any<DeletionTask>()) }
    }

    @Test
    fun `should return false when marking non-existent document for deletion`() {
        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null

        val result = documentService.markForDeletion(UUID.randomUUID(), testTenantId)

        assertFalse(result)
    }

    @Test
    fun `should get document chunks`() {
        val document = createTestDocument()
        val chunks = listOf(
            createTestChunk(0),
            createTestChunk(1),
            createTestChunk(2)
        )

        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        every { chunkRepository.findByDocumentIdOrderByChunkIndex(testDocumentId) } returns chunks

        val result = documentService.getDocumentChunks(testDocumentId, testTenantId)

        assertEquals(3, result.size)
        assertEquals(0, result[0].chunkIndex)
        assertEquals(1, result[1].chunkIndex)
        assertEquals(2, result[2].chunkIndex)
    }

    // -------------------------------------------------------------------------
    // Content ID and filename sanitization
    // -------------------------------------------------------------------------

    @Test
    fun `should sanitize path traversal sequences in filename`() {
        val file = MockMultipartFile(
            "file", "../../../etc/passwd", "text/plain", "x".toByteArray()
        )
        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null
        every { storageService.storeFile(any(), any(), any()) } returns "docs/abc/original.txt"
        every { documentRepository.save(any()) } answers { firstArg() }

        val (result, _) = documentService.uploadDocument(file, testTenantId)

        assertEquals("passwd", result.filename)
    }

    @Test
    fun `should sanitize absolute path in filename`() {
        val file = MockMultipartFile(
            "file", "/etc/shadow", "text/plain", "x".toByteArray()
        )
        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null
        every { storageService.storeFile(any(), any(), any()) } returns "docs/abc/original.txt"
        every { documentRepository.save(any()) } answers { firstArg() }

        val (result, _) = documentService.uploadDocument(file, testTenantId)

        assertEquals("shadow", result.filename)
    }

    @Test
    fun `should preserve normal filename unchanged`() {
        val file = MockMultipartFile(
            "file", "report-2024.pdf", "application/pdf", "pdf".toByteArray()
        )
        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null
        every { storageService.storeFile(any(), any(), any()) } returns "docs/abc/original.pdf"
        every { documentRepository.save(any()) } answers { firstArg() }

        val (result, _) = documentService.uploadDocument(file, testTenantId)

        assertEquals("report-2024.pdf", result.filename)
    }

    @Test
    fun `computeContentId should produce same UUID for same content and tenant`() {
        val bytes = "hello world".toByteArray()
        val (id1, hash1) = DocumentService.computeContentId("tenant-a", bytes)
        val (id2, hash2) = DocumentService.computeContentId("tenant-a", bytes)

        assertEquals(id1, id2)
        assertEquals(hash1, hash2)
    }

    @Test
    fun `computeContentId should produce different UUIDs for different tenants`() {
        val bytes = "hello world".toByteArray()
        val (id1, _) = DocumentService.computeContentId("tenant-a", bytes)
        val (id2, _) = DocumentService.computeContentId("tenant-b", bytes)

        assertTrue(id1 != id2)
    }

    // -------------------------------------------------------------------------
    // MinIO cleanup on ingestion trigger failure
    // -------------------------------------------------------------------------

    @Test
    fun `processDocument should delete MinIO files when ingestion trigger fails`() = runBlocking {
        val document = createTestDocument(status = ProcessingStatus.PENDING)
        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        every { documentRepository.save(any()) } answers { firstArg() }
        coEvery {
            ingestionServiceClient.triggerIngestion(any(), any(), any(), any(), any(), any(), any())
        } throws RuntimeException("ingestion-service is down")
        every { storageService.deleteDocumentFiles(any(), any<String>()) } just Runs

        documentService.processDocument(testDocumentId, testTenantId, "auto")

        verify(exactly = 1) { storageService.deleteDocumentFiles(testTenantId, document.filePath) }
    }

    // -------------------------------------------------------------------------
    // registerFromPath
    // -------------------------------------------------------------------------

    @Test
    fun `registerFromPath should create new document record for unknown hash`() {
        val hash = "a".repeat(64)
        val request = FromPathRequest(
            minioPath = "docs/$hash/original.txt",
            contentHash = hash,
            filename = "test.txt",
            fileSize = 100L,
            contentType = "text/plain"
        )

        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null
        every { documentRepository.save(any()) } answers { firstArg() }

        val (result, deduplicated) = documentService.registerFromPath(request, testTenantId)

        assertFalse(deduplicated)
        assertEquals("test.txt", result.filename)
        assertEquals(ProcessingStatus.PENDING, result.status)
        verify { documentRepository.save(any()) }
    }

    @Test
    fun `registerFromPath should return deduplicated=true for COMPLETED document`() {
        val hash = "b".repeat(64)
        val expectedId = DocumentService.uuidFromContentHash(hash)
        val existing = createTestDocument(id = expectedId, status = ProcessingStatus.COMPLETED)

        every { documentRepository.findByIdAndTenantId(expectedId, testTenantId) } returns existing

        val request = FromPathRequest(
            minioPath = "docs/$hash/original.txt",
            contentHash = hash,
            filename = "dedup.txt",
            fileSize = 200L
        )

        val (result, deduplicated) = documentService.registerFromPath(request, testTenantId)

        assertTrue(deduplicated)
        assertEquals(expectedId, result.id)
        verify(exactly = 0) { documentRepository.save(any()) }
    }

    @Test
    fun `registerFromPath should re-register FAILED document`() {
        val hash = "c".repeat(64)
        val expectedId = DocumentService.uuidFromContentHash(hash)
        val existing = createTestDocument(id = expectedId, status = ProcessingStatus.FAILED)

        every { documentRepository.findByIdAndTenantId(expectedId, testTenantId) } returns existing
        every { documentRepository.save(any()) } answers { firstArg() }

        val request = FromPathRequest(
            minioPath = "docs/$hash/original.txt",
            contentHash = hash,
            filename = "retry.txt",
            fileSize = 300L
        )

        val (result, deduplicated) = documentService.registerFromPath(request, testTenantId)

        assertFalse(deduplicated)
        assertEquals("retry.txt", result.filename)
        verify { documentRepository.save(any()) }
    }

    @Test
    fun `uuidFromContentHash round-trip is consistent with computeContentId`() {
        val content = "round-trip-test".toByteArray()
        val (originalId, hash) = DocumentService.computeContentId(testTenantId, content)
        val reconstructedId = DocumentService.uuidFromContentHash(hash)
        assertEquals(originalId, reconstructedId)
    }

    // -------------------------------------------------------------------------
    // markDocumentCompleted
    // -------------------------------------------------------------------------

    @Test
    fun `markDocumentCompleted should set status and chunkCount`() {
        val doc = createTestDocument(status = ProcessingStatus.PROCESSING)
        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns doc
        val saved = slot<Document>()
        every { documentRepository.save(capture(saved)) } answers { firstArg() }

        documentService.markDocumentCompleted(testDocumentId, testTenantId, 42)

        assertEquals(ProcessingStatus.COMPLETED, saved.captured.status)
        assertEquals(42, saved.captured.chunkCount)
    }

    @Test
    fun `markDocumentCompleted is a no-op for unknown document`() {
        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null

        documentService.markDocumentCompleted(UUID.randomUUID(), testTenantId, 5)

        verify(exactly = 0) { documentRepository.save(any()) }
    }

    @Test
    fun `markDocumentFailed should set status and errorMessage`() {
        val doc = createTestDocument(status = ProcessingStatus.PROCESSING)
        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns doc
        val saved = slot<Document>()
        every { documentRepository.save(capture(saved)) } answers { firstArg() }

        documentService.markDocumentFailed(testDocumentId, testTenantId, "pipeline error")

        assertEquals(ProcessingStatus.FAILED, saved.captured.status)
        assertEquals("pipeline error", saved.captured.errorMessage)
    }

    // -------------------------------------------------------------------------
    // DataSource lifecycle
    // -------------------------------------------------------------------------

    @Test
    fun `createDataSource should persist and return response with LOADING status`() {
        val request = DataSourceRequest(
            sourceType = "huggingface",
            sourceConfig = mapOf("dataset_key" to "cuad")
        )
        val saved = slot<DataSource>()
        every { dataSourceRepository.save(capture(saved)) } answers { firstArg() }

        val result = documentService.createDataSource(testTenantId, request)

        assertEquals(testTenantId, saved.captured.tenantId)
        assertEquals("huggingface", saved.captured.sourceType)
        assertEquals(DataSourceStatus.LOADING, saved.captured.status)
        assertEquals(0, saved.captured.documentCount)
        assertEquals(DataSourceStatus.LOADING, result.status)
    }

    @Test
    fun `completeDataSource should update status and documentCount`() {
        val dsId = UUID.randomUUID()
        val ds = createTestDataSource(id = dsId)
        every { dataSourceRepository.findByIdAndTenantId(dsId, testTenantId) } returns ds
        every { dataSourceRepository.save(any()) } answers { firstArg() }

        val result = documentService.completeDataSource(dsId, testTenantId, 50)

        assertEquals(DataSourceStatus.COMPLETED, ds.status)
        assertEquals(50, ds.documentCount)
        assertNotNull(ds.completedAt)
        assertEquals(DataSourceStatus.COMPLETED, result.status)
        assertEquals(50, result.documentCount)
    }

    @Test
    fun `completeDataSource throws for unknown datasource`() {
        every { dataSourceRepository.findByIdAndTenantId(any(), any()) } returns null

        assertThrows<IllegalArgumentException> {
            documentService.completeDataSource(UUID.randomUUID(), testTenantId, 10)
        }
    }

    @Test
    fun `failDataSource should set status to FAILED`() {
        val dsId = UUID.randomUUID()
        val ds = createTestDataSource(id = dsId)
        every { dataSourceRepository.findByIdAndTenantId(dsId, testTenantId) } returns ds
        every { dataSourceRepository.save(any()) } answers { firstArg() }

        val result = documentService.failDataSource(dsId, testTenantId)

        assertEquals(DataSourceStatus.FAILED, ds.status)
        assertEquals(DataSourceStatus.FAILED, result.status)
    }

    @Test
    fun `getDataSource returns null for unknown datasource`() {
        every { dataSourceRepository.findByIdAndTenantId(any(), any()) } returns null

        val result = documentService.getDataSource(UUID.randomUUID(), testTenantId)

        assertNull(result)
    }

    @Test
    fun `listDataSources returns all datasources for tenant`() {
        val sources = listOf(
            createTestDataSource(),
            createTestDataSource(id = UUID.randomUUID())
        )
        every { dataSourceRepository.findByTenantId(testTenantId) } returns sources

        val result = documentService.listDataSources(testTenantId)

        assertEquals(2, result.size)
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    private fun createTestDataSource(
        id: UUID = UUID.randomUUID(),
        status: DataSourceStatus = DataSourceStatus.LOADING
    ) = DataSource(
        id = id,
        tenantId = testTenantId,
        sourceType = "huggingface",
        sourceConfig = mapOf("dataset_key" to "cuad"),
        status = status
    )

    private fun createTestDocument(
        id: UUID = testDocumentId,
        filename: String = "test.txt",
        status: ProcessingStatus = ProcessingStatus.PENDING
    ) = Document(
        id = id,
        tenantId = testTenantId,
        filename = filename,
        contentType = "text/plain",
        fileSize = 100L,
        filePath = "docs/abc123/$filename",
        status = status,
        chunkCount = 0,
        metadata = mutableMapOf(),
        contentHash = "abc123",
        dataSourceId = null,
        createdAt = Instant.now(),
        updatedAt = Instant.now()
    )

    private fun createTestChunk(index: Int) = Chunk(
        id = UUID.randomUUID(),
        documentId = testDocumentId,
        tenantId = testTenantId,
        content = "Chunk content $index",
        chunkIndex = index,
        startChar = index * 100,
        endChar = (index + 1) * 100,
        tokenCount = 10,
        metadata = mapOf("index" to index.toString())
    )
}
