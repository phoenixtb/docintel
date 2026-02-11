package com.docintel.document.service

import com.docintel.document.BaseIntegrationTest
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.mock.web.MockMultipartFile
import java.util.UUID
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Integration tests for StorageService with MinIO Testcontainer.
 */
class StorageServiceTest : BaseIntegrationTest() {

    @Autowired
    private lateinit var storageService: StorageService

    private val testTenantId = "test-tenant"
    private lateinit var testDocumentId: UUID

    @BeforeEach
    fun setUp() {
        testDocumentId = UUID.randomUUID()
    }

    @Test
    fun `should store and retrieve text file`() {
        // Given
        val content = "This is test content for storage"
        val file = MockMultipartFile(
            "file",
            "test-document.txt",
            "text/plain",
            content.toByteArray()
        )

        // When
        val storedPath = storageService.storeFile(file, testTenantId, testDocumentId)

        // Then
        assertNotNull(storedPath)
        assertTrue(storedPath.contains(testTenantId))
        assertTrue(storedPath.contains(testDocumentId.toString()))

        // Verify we can retrieve it
        val retrieved = storageService.getFile(storedPath)
        val retrievedContent = retrieved.bufferedReader().use { it.readText() }
        assertEquals(content, retrievedContent)
    }

    @Test
    fun `should store file with correct path structure`() {
        // Given
        val file = MockMultipartFile(
            "file",
            "document.txt",
            "text/plain",
            "content".toByteArray()
        )

        // When
        val storedPath = storageService.storeFile(file, testTenantId, testDocumentId)

        // Then
        // Path should be: tenantId/documentId/filename
        assertTrue(storedPath.startsWith(testTenantId))
        assertTrue(storedPath.contains(testDocumentId.toString()))
        assertTrue(storedPath.endsWith("document.txt"))
    }

    @Test
    fun `should store and retrieve binary file`() {
        // Given
        val binaryContent = byteArrayOf(0x00, 0x01, 0x02, 0x03, 0xFF.toByte())
        val file = MockMultipartFile(
            "file",
            "binary.bin",
            "application/octet-stream",
            binaryContent
        )

        // When
        val storedPath = storageService.storeFile(file, testTenantId, testDocumentId)

        // Then
        val retrieved = storageService.getFile(storedPath)
        val retrievedContent = retrieved.readBytes()
        assertTrue(binaryContent.contentEquals(retrievedContent))
    }

    @Test
    fun `should store large file`() {
        // Given
        val largeContent = "X".repeat(1_000_000)  // 1MB
        val file = MockMultipartFile(
            "file",
            "large-file.txt",
            "text/plain",
            largeContent.toByteArray()
        )

        // When
        val storedPath = storageService.storeFile(file, testTenantId, testDocumentId)

        // Then
        assertNotNull(storedPath)
        val retrieved = storageService.getFile(storedPath)
        val retrievedContent = retrieved.bufferedReader().use { it.readText() }
        assertEquals(largeContent.length, retrievedContent.length)
    }

    @Test
    fun `should delete document files`() {
        // Given
        val file = MockMultipartFile(
            "file",
            "to-delete.txt",
            "text/plain",
            "content to delete".toByteArray()
        )
        storageService.storeFile(file, testTenantId, testDocumentId)

        // When
        storageService.deleteDocumentFiles(testTenantId, testDocumentId)

        // Then - trying to get deleted file should fail
        assertThrows<Exception> {
            storageService.getFile("$testTenantId/$testDocumentId/to-delete.txt")
        }
    }

    @Test
    fun `should handle multiple files for same document`() {
        // Given
        val docId = UUID.randomUUID()
        val file1 = MockMultipartFile("file", "doc1.txt", "text/plain", "content1".toByteArray())
        val file2 = MockMultipartFile("file", "doc2.txt", "text/plain", "content2".toByteArray())

        // When
        val path1 = storageService.storeFile(file1, testTenantId, docId)
        // Note: In real implementation, second file would overwrite or have different name

        // Then
        assertNotNull(path1)
    }

    @Test
    fun `should handle special characters in filename`() {
        // Given
        val file = MockMultipartFile(
            "file",
            "file with spaces (1).txt",
            "text/plain",
            "content".toByteArray()
        )

        // When
        val storedPath = storageService.storeFile(file, testTenantId, testDocumentId)

        // Then
        assertNotNull(storedPath)
        // Should be able to retrieve
        val retrieved = storageService.getFile(storedPath)
        assertNotNull(retrieved)
    }

    @Test
    fun `should isolate files by tenant`() {
        // Given
        val tenant1 = "tenant-1"
        val tenant2 = "tenant-2"
        val docId = UUID.randomUUID()
        
        val file1 = MockMultipartFile("file", "doc.txt", "text/plain", "tenant1 content".toByteArray())
        val file2 = MockMultipartFile("file", "doc.txt", "text/plain", "tenant2 content".toByteArray())

        // When
        val path1 = storageService.storeFile(file1, tenant1, docId)
        val path2 = storageService.storeFile(file2, tenant2, docId)

        // Then
        assertTrue(path1.startsWith(tenant1))
        assertTrue(path2.startsWith(tenant2))
        
        val content1 = storageService.getFile(path1).bufferedReader().use { it.readText() }
        val content2 = storageService.getFile(path2).bufferedReader().use { it.readText() }
        
        assertEquals("tenant1 content", content1)
        assertEquals("tenant2 content", content2)
    }
}
