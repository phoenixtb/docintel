package com.docintel.document.service

import com.docintel.document.BaseIntegrationTest
import com.docintel.document.tenant.TenantContextHolder
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.mock.web.MockMultipartFile
import software.amazon.awssdk.core.sync.RequestBody
import software.amazon.awssdk.services.s3.S3Client
import software.amazon.awssdk.services.s3.model.NoSuchKeyException
import kotlin.test.assertNotNull
import kotlin.test.assertTrue
import kotlin.test.assertEquals

/**
 * Integration tests for StorageService against a real S3 server (VersityGW Testcontainer).
 *
 * Content-addressable path convention: docs/{content_hash}/original.{ext}
 * Tenant isolation is at the bucket level: bucket = "docintel-{tenantId}".
 */
class StorageServiceTest : BaseIntegrationTest() {

    @Autowired
    private lateinit var storageService: StorageService

    @Autowired
    private lateinit var s3: S3Client

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

        // Same hash → same path (idempotent PUT)
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

        assertThrows<NoSuchKeyException> {
            storageService.getFile(storedPath)
        }
    }

    @Test
    fun `getFile on a missing key throws NoSuchKeyException`() {
        // Ensure the bucket exists so the failure is about the key, not the bucket.
        storageService.storeFile(MockMultipartFile("file", "x.txt", "text/plain", "x".toByteArray()), testTenantId, "f".repeat(64))

        assertThrows<NoSuchKeyException> {
            storageService.getFile("docs/${"0".repeat(64)}/original.txt")
        }
    }

    @Test
    fun `deleteDocumentFiles removes every object under the document prefix and spares siblings`() {
        val tenant = "tenant-prefix"
        val doomed = storageService.storeFile(MockMultipartFile("file", "a.pdf", "application/pdf", "a".toByteArray()), tenant, "1".repeat(64))
        val sibling = storageService.storeFile(MockMultipartFile("file", "b.pdf", "application/pdf", "b".toByteArray()), tenant, "2".repeat(64))
        val bucket = StorageService.bucketFor(tenant)
        // Derived artefacts that live next to the original (e.g. page renders).
        s3.putObject({ it.bucket(bucket).key("docs/${"1".repeat(64)}/pages/1.png") }, RequestBody.fromString("png"))

        storageService.deleteDocumentFiles(tenant, doomed)

        assertEquals(emptyList(), keysUnder(bucket, "docs/${"1".repeat(64)}/"))
        assertEquals(listOf(sibling), keysUnder(bucket, "docs/${"2".repeat(64)}/"))
    }

    @Test
    fun `deleteDocumentFiles deletes more than one DeleteObjects batch`() {
        val tenant = "tenant-batch"
        val hash = "3".repeat(64)
        val original = storageService.storeFile(MockMultipartFile("file", "big.txt", "text/plain", "x".toByteArray()), tenant, hash)
        val bucket = StorageService.bucketFor(tenant)
        repeat(1_005) { i ->
            s3.putObject({ it.bucket(bucket).key("docs/$hash/shards/$i.json") }, RequestBody.fromString("{}"))
        }

        storageService.deleteDocumentFiles(tenant, original)

        assertEquals(emptyList(), keysUnder(bucket, "docs/$hash/"))
    }

    @Test
    fun `deleteDocumentFiles is a no-op when the tenant bucket does not exist`() {
        storageService.deleteDocumentFiles("tenant-never-uploaded", "docs/${"4".repeat(64)}/original.txt")
    }

    @Test
    fun `deleteDocumentFiles is idempotent`() {
        val tenant = "tenant-idem"
        val path = storageService.storeFile(MockMultipartFile("file", "d.txt", "text/plain", "d".toByteArray()), tenant, "5".repeat(64))

        storageService.deleteDocumentFiles(tenant, path)
        storageService.deleteDocumentFiles(tenant, path)

        assertEquals(emptyList(), keysUnder(StorageService.bucketFor(tenant), "docs/${"5".repeat(64)}/"))
    }

    private fun keysUnder(bucket: String, prefix: String): List<String> =
        s3.listObjectsV2Paginator { it.bucket(bucket).prefix(prefix) }.contents().map { it.key() }

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
