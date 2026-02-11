package com.docintel.document.service

import com.docintel.document.entity.Chunk
import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.repository.ChunkRepository
import com.docintel.document.repository.DocumentRepository
import io.mockk.*
import io.mockk.impl.annotations.MockK
import io.mockk.junit5.MockKExtension
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.extension.ExtendWith
import org.springframework.data.domain.PageImpl
import org.springframework.data.domain.Pageable
import org.springframework.mock.web.MockMultipartFile
import java.io.ByteArrayInputStream
import java.time.Instant
import java.util.UUID
import kotlin.test.assertEquals
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
    private lateinit var chunkRepository: ChunkRepository

    @MockK
    private lateinit var storageService: StorageService

    @MockK
    private lateinit var textExtractionService: TextExtractionService

    @MockK
    private lateinit var ragServiceClient: RagServiceClient

    private lateinit var documentService: DocumentService

    private val testTenantId = "test-tenant"
    private val testDocumentId = UUID.randomUUID()

    @BeforeEach
    fun setUp() {
        documentService = DocumentService(
            documentRepository,
            chunkRepository,
            storageService,
            textExtractionService,
            ragServiceClient
        )
    }

    @Test
    fun `should upload document and save to repository`() {
        // Given
        val file = MockMultipartFile(
            "file",
            "test-doc.txt",
            "text/plain",
            "Test content".toByteArray()
        )
        
        val storedPath = "$testTenantId/$testDocumentId/test-doc.txt"
        
        every { storageService.storeFile(any(), any(), any()) } returns storedPath
        every { documentRepository.save(any()) } answers { firstArg() }

        // When
        val result = documentService.uploadDocument(file, testTenantId, emptyMap())

        // Then
        assertNotNull(result)
        assertEquals("test-doc.txt", result.filename)
        assertEquals(ProcessingStatus.PENDING, result.status)
        
        verify { storageService.storeFile(file, testTenantId, any()) }
        verify { documentRepository.save(any()) }
    }

    @Test
    fun `should upload document with metadata`() {
        // Given
        val file = MockMultipartFile("file", "doc.txt", "text/plain", "content".toByteArray())
        val metadata = mapOf("author" to "John", "department" to "HR")
        
        every { storageService.storeFile(any(), any(), any()) } returns "path/to/file"
        every { documentRepository.save(any()) } answers { firstArg() }

        // When
        val result = documentService.uploadDocument(file, testTenantId, metadata)

        // Then
        assertEquals("John", result.metadata["author"])
        assertEquals("HR", result.metadata["department"])
    }

    @Test
    fun `should get document by id and tenant`() {
        // Given
        val document = createTestDocument()
        
        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        every { chunkRepository.findByDocumentIdOrderByChunkIndex(testDocumentId) } returns emptyList()

        // When
        val result = documentService.getDocument(testDocumentId, testTenantId, false)

        // Then
        assertNotNull(result)
        assertEquals(testDocumentId, result.id)
        assertEquals("test.txt", result.filename)
    }

    @Test
    fun `should return null for non-existent document`() {
        // Given
        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null

        // When
        val result = documentService.getDocument(UUID.randomUUID(), testTenantId, false)

        // Then
        assertNull(result)
    }

    @Test
    fun `should get document with chunks when requested`() {
        // Given
        val document = createTestDocument()
        
        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        every { chunkRepository.findByDocumentIdOrderByChunkIndex(testDocumentId) } returns listOf(
            createTestChunk(0),
            createTestChunk(1)
        )

        // When
        val result = documentService.getDocument(testDocumentId, testTenantId, includeChunks = true)

        // Then
        assertNotNull(result)
        assertNotNull(result.chunks)
        assertEquals(2, result.chunks!!.size)
    }

    @Test
    fun `should list documents for tenant`() {
        // Given
        val documents = listOf(
            createTestDocument(),
            createTestDocument(id = UUID.randomUUID(), filename = "doc2.txt")
        )
        val page = PageImpl(documents)
        
        every { documentRepository.findByTenantId(testTenantId, any()) } returns page

        // When
        val result = documentService.listDocuments(testTenantId, null, Pageable.unpaged())

        // Then
        assertEquals(2, result.totalElements)
    }

    @Test
    fun `should list documents filtered by status`() {
        // Given
        val documents = listOf(createTestDocument(status = ProcessingStatus.COMPLETED))
        val page = PageImpl(documents)
        
        every { 
            documentRepository.findByTenantIdAndStatus(testTenantId, ProcessingStatus.COMPLETED, any()) 
        } returns page

        // When
        val result = documentService.listDocuments(testTenantId, ProcessingStatus.COMPLETED, Pageable.unpaged())

        // Then
        assertEquals(1, result.totalElements)
        assertEquals(ProcessingStatus.COMPLETED, result.content[0].status)
    }

    @Test
    fun `should delete document and associated data`() = runBlocking {
        // Given
        val document = createTestDocument()
        
        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        coEvery { ragServiceClient.deleteDocumentVectors(testTenantId, testDocumentId) } returns true
        every { chunkRepository.deleteByDocumentId(testDocumentId) } returns 3L
        every { storageService.deleteDocumentFiles(testTenantId, testDocumentId) } just Runs
        every { documentRepository.delete(document) } just Runs

        // When
        val result = documentService.deleteDocument(testDocumentId, testTenantId)

        // Then
        assertTrue(result)
        verify { chunkRepository.deleteByDocumentId(testDocumentId) }
        verify { storageService.deleteDocumentFiles(testTenantId, testDocumentId) }
        verify { documentRepository.delete(document) }
    }

    @Test
    fun `should return false when deleting non-existent document`() = runBlocking {
        // Given
        every { documentRepository.findByIdAndTenantId(any(), any()) } returns null

        // When
        val result = documentService.deleteDocument(UUID.randomUUID(), testTenantId)

        // Then
        assertTrue(!result)
    }

    @Test
    fun `should get document chunks`() {
        // Given
        val document = createTestDocument()
        val chunks = listOf(
            createTestChunk(0),
            createTestChunk(1),
            createTestChunk(2)
        )
        
        every { documentRepository.findByIdAndTenantId(testDocumentId, testTenantId) } returns document
        every { chunkRepository.findByDocumentIdOrderByChunkIndex(testDocumentId) } returns chunks

        // When
        val result = documentService.getDocumentChunks(testDocumentId, testTenantId)

        // Then
        assertEquals(3, result.size)
        assertEquals(0, result[0].chunkIndex)
        assertEquals(1, result[1].chunkIndex)
        assertEquals(2, result[2].chunkIndex)
    }

    // Helper functions to create test entities
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
        filePath = "$testTenantId/$id/$filename",
        status = status,
        chunkCount = 0,
        metadata = mutableMapOf(),
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
