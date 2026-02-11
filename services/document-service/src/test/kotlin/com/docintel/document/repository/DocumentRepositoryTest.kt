package com.docintel.document.repository

import com.docintel.document.BaseIntegrationTest
import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.data.domain.PageRequest
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/**
 * Repository tests for DocumentRepository.
 * Uses Testcontainers for PostgreSQL.
 */
class DocumentRepositoryTest : BaseIntegrationTest() {

    @Autowired
    private lateinit var documentRepository: DocumentRepository

    private val tenantA = "tenant-a"
    private val tenantB = "tenant-b"

    @BeforeEach
    fun setUp() {
        documentRepository.deleteAll()
    }

    @Test
    fun `should save and retrieve document`() {
        // Given
        val document = createDocument()

        // When
        val saved = documentRepository.save(document)
        val retrieved = documentRepository.findById(saved.id)

        // Then
        assertTrue(retrieved.isPresent)
        assertEquals(document.filename, retrieved.get().filename)
        assertEquals(document.tenantId, retrieved.get().tenantId)
    }

    @Test
    fun `should find document by id and tenant`() {
        // Given
        val document = createDocument(tenantId = tenantA)
        val saved = documentRepository.save(document)

        // When
        val found = documentRepository.findByIdAndTenantId(saved.id, tenantA)

        // Then
        assertNotNull(found)
        assertEquals(saved.id, found.id)
    }

    @Test
    fun `should not find document for wrong tenant`() {
        // Given
        val document = createDocument(tenantId = tenantA)
        val saved = documentRepository.save(document)

        // When
        val found = documentRepository.findByIdAndTenantId(saved.id, tenantB)

        // Then
        assertNull(found)
    }

    @Test
    fun `should find documents by tenant with pagination`() {
        // Given
        repeat(5) { 
            documentRepository.save(createDocument(tenantId = tenantA, filename = "doc-$it.txt"))
        }
        repeat(3) {
            documentRepository.save(createDocument(tenantId = tenantB, filename = "other-$it.txt"))
        }

        // When
        val page = documentRepository.findByTenantId(tenantA, PageRequest.of(0, 3))

        // Then
        assertEquals(3, page.content.size)
        assertEquals(5, page.totalElements)
        assertEquals(2, page.totalPages)
        assertTrue(page.content.all { it.tenantId == tenantA })
    }

    @Test
    fun `should find documents by tenant and status`() {
        // Given
        documentRepository.save(createDocument(tenantId = tenantA, status = ProcessingStatus.PENDING))
        documentRepository.save(createDocument(tenantId = tenantA, status = ProcessingStatus.COMPLETED))
        documentRepository.save(createDocument(tenantId = tenantA, status = ProcessingStatus.COMPLETED))
        documentRepository.save(createDocument(tenantId = tenantA, status = ProcessingStatus.FAILED))

        // When
        val completed = documentRepository.findByTenantIdAndStatus(
            tenantA, ProcessingStatus.COMPLETED, PageRequest.of(0, 10)
        )

        // Then
        assertEquals(2, completed.content.size)
        assertTrue(completed.content.all { it.status == ProcessingStatus.COMPLETED })
    }

    @Test
    fun `should update document status`() {
        // Given
        val document = createDocument(status = ProcessingStatus.PENDING)
        val saved = documentRepository.save(document)

        // When
        saved.status = ProcessingStatus.COMPLETED
        saved.chunkCount = 10
        documentRepository.save(saved)

        // Then
        val updated = documentRepository.findById(saved.id).get()
        assertEquals(ProcessingStatus.COMPLETED, updated.status)
        assertEquals(10, updated.chunkCount)
    }

    @Test
    fun `should store and retrieve metadata`() {
        // Given
        val metadata = mutableMapOf(
            "author" to "John Doe",
            "department" to "HR",
            "version" to "1.0"
        )
        val document = createDocument(metadata = metadata)

        // When
        val saved = documentRepository.save(document)
        val retrieved = documentRepository.findById(saved.id).get()

        // Then
        assertEquals("John Doe", retrieved.metadata["author"])
        assertEquals("HR", retrieved.metadata["department"])
        assertEquals("1.0", retrieved.metadata["version"])
    }

    @Test
    fun `should delete document`() {
        // Given
        val document = createDocument()
        val saved = documentRepository.save(document)

        // When
        documentRepository.delete(saved)

        // Then
        assertTrue(documentRepository.findById(saved.id).isEmpty)
    }

    @Test
    fun `should count documents by tenant`() {
        // Given
        repeat(5) { documentRepository.save(createDocument(tenantId = tenantA)) }
        repeat(3) { documentRepository.save(createDocument(tenantId = tenantB)) }

        // When
        val countA = documentRepository.findByTenantId(tenantA, PageRequest.of(0, 100)).totalElements
        val countB = documentRepository.findByTenantId(tenantB, PageRequest.of(0, 100)).totalElements

        // Then
        assertEquals(5, countA)
        assertEquals(3, countB)
    }

    @Test
    fun `should handle large metadata`() {
        // Given
        val largeMetadata = (1..100).associate { "key$it" to "value$it" }.toMutableMap()
        val document = createDocument(metadata = largeMetadata)

        // When
        val saved = documentRepository.save(document)
        val retrieved = documentRepository.findById(saved.id).get()

        // Then
        assertEquals(100, retrieved.metadata.size)
    }

    // Helper function
    private fun createDocument(
        tenantId: String = tenantA,
        filename: String = "test-${UUID.randomUUID()}.txt",
        status: ProcessingStatus = ProcessingStatus.PENDING,
        metadata: MutableMap<String, String> = mutableMapOf()
    ) = Document(
        id = UUID.randomUUID(),
        tenantId = tenantId,
        filename = filename,
        contentType = "text/plain",
        fileSize = 100L,
        filePath = "$tenantId/${UUID.randomUUID()}/$filename",
        status = status,
        chunkCount = 0,
        metadata = metadata
    )
}
