package com.docintel.document.service

import com.docintel.document.BaseIntegrationTest
import com.docintel.document.tenant.TenantContextHolder
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.mock.web.MockMultipartFile
import kotlin.test.assertNotNull
import kotlin.test.assertTrue
import kotlin.test.assertEquals

/**
 * Integration tests for StorageService against MinIO Testcontainer.
 *
 * Content-addressable path convention: docs/{content_hash}/original.{ext}
 * Tenant isolation is at the MinIO bucket level: bucket = "docintel-{tenantId}".
 */
class StorageServiceTest : BaseIntegrationTest() {

    @Autowired
    private lateinit var storageService: StorageService

    private val testTenantId = "test-tenant"
    private val testContentHash = "a".repeat(64)   // 64-char hex-like string for tests

    @BeforeEach
    fun setUp() {
        TenantContextHolder.setTenantId(testTenantId)
    }

    @AfterEach
    fun tearDown() {
        TenantContextHolder.clear()
    }

    @Test
    fun `should store and retrieve text file`() {
        val content = "This is test content for storage"
        val file = MockMultipartFile("file", "test-document.txt", "text/plain", content.toByteArray())

        val storedPath = storageService.storeFile(file, testTenantId, testContentHash)

        // Path follows content-addressable convention: docs/{hash}/original.txt
        assertNotNull(storedPath)
        assertTrue(storedPath.startsWith("docs/"))
        assertTrue(storedPath.endsWith("original.txt"))

        val retrievedContent = storageService.getFile(storedPath).bufferedReader().use { it.readText() }
        assertEquals(content, retrievedContent)
    }

    @Test
    fun `should store file with correct content-addressable path structure`() {
        val file = MockMultipartFile("file", "document.pdf", "application/pdf", "content".toByteArray())

        val storedPath = storageService.storeFile(file, testTenantId, testContentHash)

        // Structure: docs/{hash}/original.pdf
        assertTrue(storedPath.startsWith("docs/$testContentHash/"))
        assertTrue(storedPath.endsWith("original.pdf"))
    }

    @Test
    fun `should idempotently store same content at same path`() {
        val content = "duplicate content"
        val hash = "b".repeat(64)
        val file1 = MockMultipartFile("file", "doc.txt", "text/plain", content.toByteArray())
        val file2 = MockMultipartFile("file", "doc.txt", "text/plain", content.toByteArray())

        val path1 = storageService.storeFile(file1, testTenantId, hash)
        val path2 = storageService.storeFile(file2, testTenantId, hash)

        // Same hash → same path (idempotent MinIO PUT)
        assertEquals(path1, path2)
    }

    @Test
    fun `should store and retrieve allowed binary file`() {
        val binaryContent = byteArrayOf(0x25, 0x50, 0x44, 0x46)  // "%PDF" magic bytes
        val file = MockMultipartFile("file", "opaque.pdf", "application/pdf", binaryContent)

        val storedPath = storageService.storeFile(file, testTenantId, testContentHash)
        val retrievedContent = storageService.getFile(storedPath).readBytes()

        assertTrue(binaryContent.contentEquals(retrievedContent))
    }

    @Test
    fun `should reject disallowed file extensions`() {
        val file = MockMultipartFile("file", "script.exe", "application/octet-stream", "content".toByteArray())

        assertThrows<IllegalArgumentException> {
            storageService.storeFile(file, testTenantId, testContentHash)
        }
    }

    @Test
    fun `should store large file`() {
        val largeContent = "X".repeat(1_000_000)
        val file = MockMultipartFile("file", "large-file.txt", "text/plain", largeContent.toByteArray())
        val hash = "c".repeat(64)

        val storedPath = storageService.storeFile(file, testTenantId, hash)

        assertNotNull(storedPath)
        val retrievedContent = storageService.getFile(storedPath).bufferedReader().use { it.readText() }
        assertEquals(largeContent.length, retrievedContent.length)
    }

    @Test
    fun `should delete document files by path`() {
        val file = MockMultipartFile("file", "to-delete.txt", "text/plain", "content to delete".toByteArray())
        val hash = "d".repeat(64)
        val storedPath = storageService.storeFile(file, testTenantId, hash)

        storageService.deleteDocumentFiles(testTenantId, storedPath)

        assertThrows<Exception> {
            storageService.getFile(storedPath)
        }
    }

    @Test
    fun `should handle special characters in filename`() {
        val file = MockMultipartFile("file", "file with spaces (1).txt", "text/plain", "content".toByteArray())

        val storedPath = storageService.storeFile(file, testTenantId, testContentHash)

        assertNotNull(storedPath)
        assertTrue(storedPath.endsWith("original.txt"))
        assertNotNull(storageService.getFile(storedPath))
    }

    @Test
    fun `should isolate files by tenant`() {
        val tenant1 = "tenant-alpha"
        val tenant2 = "tenant-beta"
        val hash = "e".repeat(64)

        val file1 = MockMultipartFile("file", "doc.txt", "text/plain", "alpha content".toByteArray())
        val file2 = MockMultipartFile("file", "doc.txt", "text/plain", "beta content".toByteArray())

        val path1 = storageService.storeFile(file1, tenant1, hash)
        val path2 = storageService.storeFile(file2, tenant2, hash)

        // Same hash → same path; isolation is at bucket level (docintel-{tenantId})
        assertEquals(path1, path2)

        TenantContextHolder.setTenantId(tenant1)
        val content1 = storageService.getFile(path1).bufferedReader().use { it.readText() }
        assertEquals("alpha content", content1)

        TenantContextHolder.setTenantId(tenant2)
        val content2 = storageService.getFile(path2).bufferedReader().use { it.readText() }
        assertEquals("beta content", content2)

        TenantContextHolder.setTenantId(testTenantId)
    }
}
