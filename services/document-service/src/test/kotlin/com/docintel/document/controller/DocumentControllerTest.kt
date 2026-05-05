package com.docintel.document.controller

import com.docintel.document.BaseIntegrationTest
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.repository.ChunkRepository
import com.docintel.document.repository.DataSourceRepository
import com.docintel.document.repository.DocumentRepository
import com.docintel.document.service.*
import com.fasterxml.jackson.databind.ObjectMapper
import com.ninjasquad.springmockk.MockkBean
import io.mockk.coEvery
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.http.MediaType
import org.springframework.mock.web.MockMultipartFile
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.*
import java.util.UUID

/**
 * Integration tests for DocumentController.
 * Uses Testcontainers for PostgreSQL and MinIO (started in BaseIntegrationTest).
 *
 * - Controller reads the tenant from the X-Tenant-Id *header* (@RequestHeader), not a query param.
 * - deleteDocument is a suspend fun so it returns a DeferredResult; MockMvc requires
 *   an asyncDispatch round-trip to get the actual HTTP status.
 */
@AutoConfigureMockMvc(addFilters = false)
class DocumentControllerTest : BaseIntegrationTest() {

    @Autowired private lateinit var mockMvc: MockMvc
    @Autowired private lateinit var objectMapper: ObjectMapper
    @Autowired private lateinit var documentRepository: DocumentRepository
    @Autowired private lateinit var chunkRepository: ChunkRepository
    @Autowired private lateinit var dataSourceRepository: DataSourceRepository

    @MockkBean private lateinit var vectorStoreClient: VectorStoreClient
    @MockkBean(relaxed = true) private lateinit var documentStreamPublisher: com.docintel.document.messaging.DocumentStreamPublisher

    private val testTenantId = "integration-test-tenant"

    @BeforeEach
    fun setUp() {
        chunkRepository.deleteAll()
        documentRepository.deleteAll()
        dataSourceRepository.deleteAll()

        coEvery { vectorStoreClient.deleteDocumentVectors(any(), any()) } returns true
        coEvery { vectorStoreClient.deleteTenantVectors(any()) } returns true
    }

    // ─── Upload ───────────────────────────────────────────────────────────────

    @Test
    fun `POST should upload document and return 201`() {
        val file = MockMultipartFile("file", "test-document.txt", "text/plain",
            "This is test content for upload.".toByteArray())

        mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.filename").value("test-document.txt"))
            .andExpect(jsonPath("$.status").value("PENDING"))
            .andExpect(jsonPath("$.id").exists())
    }

    @Test
    fun `POST should upload document with domain override`() {
        val file = MockMultipartFile("file", "hr-policy.txt", "text/plain",
            "HR Policy content".toByteArray())

        mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .header("X-Tenant-Id", testTenantId)
                .param("domain", "hr_policy")
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.filename").value("hr-policy.txt"))
    }

    @Test
    fun `POST should upload document with metadata`() {
        val file = MockMultipartFile("file", "metadata-doc.txt", "text/plain",
            "Content".toByteArray())
        val metadata = """{"author": "Test Author", "version": "1.0"}"""

        mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .header("X-Tenant-Id", testTenantId)
                .param("metadata", metadata)
        )
            .andExpect(status().isCreated)
    }

    // ─── Get ──────────────────────────────────────────────────────────────────

    @Test
    fun `GET should return document by id`() {
        val docId = uploadTestDocument()

        mockMvc.perform(
            get("/internal/documents/$docId")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.id").value(docId.toString()))
            .andExpect(jsonPath("$.filename").exists())
    }

    @Test
    fun `GET should return 404 for non-existent document`() {
        mockMvc.perform(
            get("/internal/documents/${UUID.randomUUID()}")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isNotFound)
    }

    @Test
    fun `GET should not return document for wrong tenant`() {
        val docId = uploadTestDocument()

        mockMvc.perform(
            get("/internal/documents/$docId")
                .header("X-Tenant-Id", "different-tenant")
        )
            .andExpect(status().isNotFound)
    }

    // ─── List ─────────────────────────────────────────────────────────────────

    @Test
    fun `GET list should return paginated documents`() {
        repeat(5) { uploadTestDocument(filename = "doc-$it.txt") }

        mockMvc.perform(
            get("/internal/documents")
                .header("X-Tenant-Id", testTenantId)
                .param("size", "3")
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.content").isArray)
            .andExpect(jsonPath("$.content.length()").value(3))
            .andExpect(jsonPath("$.totalElements").value(5))
            .andExpect(jsonPath("$.totalPages").value(2))
    }

    @Test
    fun `GET list should filter by status`() {
        uploadTestDocument()

        mockMvc.perform(
            get("/internal/documents")
                .header("X-Tenant-Id", testTenantId)
                .param("status", "PENDING")
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.content").isArray)
    }

    @Test
    fun `GET list should return empty for tenant with no documents`() {
        mockMvc.perform(
            get("/internal/documents")
                .header("X-Tenant-Id", "empty-tenant")
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.content").isEmpty)
            .andExpect(jsonPath("$.totalElements").value(0))
    }

    // ─── Delete ───────────────────────────────────────────────────────────────

    @Test
    fun `DELETE should remove document and return 204`() {
        val docId = uploadTestDocument()

        // deleteDocument is suspend → DeferredResult → needs asyncDispatch
        val asyncResult = mockMvc.perform(
            delete("/internal/documents/$docId")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(request().asyncStarted())
            .andReturn()

        mockMvc.perform(asyncDispatch(asyncResult))
            .andExpect(status().isNoContent)

        // Verify document is gone
        mockMvc.perform(
            get("/internal/documents/$docId")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isNotFound)
    }

    @Test
    fun `DELETE should return 404 for non-existent document`() {
        val asyncResult = mockMvc.perform(
            delete("/internal/documents/${UUID.randomUUID()}")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(request().asyncStarted())
            .andReturn()

        mockMvc.perform(asyncDispatch(asyncResult))
            .andExpect(status().isNotFound)
    }

    @Test
    fun `DELETE should return 503 when vector store is unavailable`() {
        val docId = uploadTestDocument("vector-fail-doc.txt")

        coEvery { vectorStoreClient.deleteDocumentVectors(any(), any()) } returns false

        val asyncResult = mockMvc.perform(
            delete("/internal/documents/$docId")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(request().asyncStarted())
            .andReturn()

        mockMvc.perform(asyncDispatch(asyncResult))
            .andExpect(status().isServiceUnavailable)
            .andExpect(jsonPath("$.error").value("Vector store unavailable"))

        coEvery { vectorStoreClient.deleteDocumentVectors(any(), any()) } returns true
    }

    // ─── File content ─────────────────────────────────────────────────────────

    @Test
    fun `should upload and retrieve actual test document`() {
        val content = javaClass.getResource("/documents/hr_policy_leave.txt")?.readText()
            ?: throw IllegalStateException("Test file not found")

        val file = MockMultipartFile("file", "hr_policy_leave.txt", "text/plain",
            content.toByteArray())

        val uploadResult = mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .header("X-Tenant-Id", testTenantId)
                .param("domain", "hr_policy")
        )
            .andExpect(status().isCreated)
            .andReturn()

        val docId = objectMapper.readTree(uploadResult.response.contentAsString)["id"].asText()

        mockMvc.perform(
            get("/internal/documents/$docId")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.filename").value("hr_policy_leave.txt"))
    }

    // ─── From-path (stream-based registration) ────────────────────────────────

    @Test
    fun `POST from-path should register new document and return 201`() {
        val contentHash = "1".repeat(64)
        val body = mapOf(
            "minioPath"   to "docs/$contentHash/original.txt",
            "contentHash" to contentHash,
            "filename"    to "stream-doc.txt",
            "fileSize"    to 512,
            "contentType" to "text/plain"
        )

        mockMvc.perform(
            post("/internal/documents/from-path")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(body))
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.document.filename").value("stream-doc.txt"))
            .andExpect(jsonPath("$.document.status").value("PENDING"))
            .andExpect(jsonPath("$.deduplicated").value(false))
            .andExpect(jsonPath("$.document.id").exists())
    }

    @Test
    fun `POST from-path second call with same hash returns 200 deduplicated=true`() {
        val contentHash = "2".repeat(64)
        val body = mapOf(
            "minioPath"   to "docs/$contentHash/original.txt",
            "contentHash" to contentHash,
            "filename"    to "dedup-doc.txt",
            "fileSize"    to 256
        )
        val json = objectMapper.writeValueAsString(body)

        mockMvc.perform(
            post("/internal/documents/from-path")
                .contentType(MediaType.APPLICATION_JSON)
                .content(json)
                .header("X-Tenant-Id", testTenantId)
        ).andExpect(status().isCreated)

        mockMvc.perform(
            post("/internal/documents/from-path")
                .contentType(MediaType.APPLICATION_JSON)
                .content(json)
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.deduplicated").value(true))
    }

    @Test
    fun `POST from-path isolates tenants — same hash different tenant returns 201`() {
        val contentHash = "3".repeat(64)
        val body = mapOf(
            "minioPath"   to "docs/$contentHash/original.txt",
            "contentHash" to contentHash,
            "filename"    to "shared.txt",
            "fileSize"    to 100
        )
        val json = objectMapper.writeValueAsString(body)

        mockMvc.perform(
            post("/internal/documents/from-path")
                .contentType(MediaType.APPLICATION_JSON)
                .content(json)
                .header("X-Tenant-Id", "tenant-alpha")
        ).andExpect(status().isCreated)

        // Same hash, different tenant → different document ID → new record
        mockMvc.perform(
            post("/internal/documents/from-path")
                .contentType(MediaType.APPLICATION_JSON)
                .content(json)
                .header("X-Tenant-Id", "tenant-beta")
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.deduplicated").value(false))
    }

    // ─── DataSource CRUD ──────────────────────────────────────────────────────

    @Test
    fun `POST data-sources should create and return 201`() {
        mockMvc.perform(
            post("/internal/documents/data-sources")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"sourceType":"huggingface","sourceConfig":{"dataset_key":"cuad"}}""")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.id").exists())
            .andExpect(jsonPath("$.status").value("LOADING"))
            .andExpect(jsonPath("$.sourceType").value("huggingface"))
            .andExpect(jsonPath("$.documentCount").value(0))
    }

    @Test
    fun `GET data-sources should return list for tenant`() {
        createTestDataSource()
        createTestDataSource()

        mockMvc.perform(
            get("/internal/documents/data-sources")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.length()").value(2))
    }

    @Test
    fun `GET data-sources by id should return datasource`() {
        val dsId = createTestDataSource()

        mockMvc.perform(
            get("/internal/documents/data-sources/$dsId")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.id").value(dsId.toString()))
            .andExpect(jsonPath("$.status").value("LOADING"))
    }

    @Test
    fun `GET data-sources by id should return 404 for wrong tenant`() {
        val dsId = createTestDataSource()

        mockMvc.perform(
            get("/internal/documents/data-sources/$dsId")
                .header("X-Tenant-Id", "wrong-tenant")
        )
            .andExpect(status().isNotFound)
    }

    @Test
    fun `POST data-sources complete should update status to COMPLETED`() {
        val dsId = createTestDataSource()

        mockMvc.perform(
            post("/internal/documents/data-sources/$dsId/complete")
                .header("X-Tenant-Id", testTenantId)
                .param("document_count", "42")
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.status").value("COMPLETED"))
            .andExpect(jsonPath("$.documentCount").value(42))
    }

    @Test
    fun `POST data-sources fail should update status to FAILED`() {
        val dsId = createTestDataSource()

        mockMvc.perform(
            post("/internal/documents/data-sources/$dsId/fail")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.status").value("FAILED"))
    }

    // ─── Helper ───────────────────────────────────────────────────────────────

    private fun createTestDataSource(): UUID {
        val result = mockMvc.perform(
            post("/internal/documents/data-sources")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"sourceType":"huggingface","sourceConfig":{"dataset_key":"techqa"}}""")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isCreated)
            .andReturn()
        return UUID.fromString(
            objectMapper.readTree(result.response.contentAsString)["id"].asText()
        )
    }

    private fun uploadTestDocument(filename: String = "test.txt"): UUID {
        val file = MockMultipartFile("file", filename, "text/plain",
            "Test content for $filename".toByteArray())

        val result = mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isCreated)
            .andReturn()

        return UUID.fromString(
            objectMapper.readTree(result.response.contentAsString)["id"].asText()
        )
    }

    // ─── Cleanup ──────────────────────────────────────────────────────────────

    @Test
    fun `POST cleanup preview returns matchCount for uploaded docs`() {
        uploadTestDocument("doc-a.txt")
        uploadTestDocument("doc-b.txt")

        mockMvc.perform(
            post("/internal/documents/cleanup/preview")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{}")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.matchCount").value(2))
            .andExpect(jsonPath("$.tenantId").value(testTenantId))
    }

    @Test
    fun `POST cleanup preview with status filter returns only matching docs`() {
        uploadTestDocument("pending.txt")

        mockMvc.perform(
            post("/internal/documents/cleanup/preview")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"statuses":["FAILED"]}""")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.matchCount").value(0))
    }

    @Test
    fun `POST cleanup preview cross-tenant without delete_all role returns 403`() {
        mockMvc.perform(
            post("/internal/documents/cleanup/preview")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""{"targetTenantId":"other-tenant"}""")
                .header("X-Tenant-Id", testTenantId)
                .header("X-User-Roles", "documents:r,documents:delete")
        )
            .andExpect(status().isForbidden)
    }

    @Test
    fun `POST cleanup jobs returns 202 with jobId`() {
        mockMvc.perform(
            post("/internal/documents/cleanup/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{}")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isAccepted)
            .andExpect(jsonPath("$.jobId").exists())
            .andExpect(jsonPath("$.tenantId").value(testTenantId))
    }

    @Test
    fun `POST cleanup jobs returns 409 when another job is active`() {
        mockMvc.perform(
            post("/internal/documents/cleanup/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{}")
                .header("X-Tenant-Id", testTenantId)
        ).andExpect(status().isAccepted)

        mockMvc.perform(
            post("/internal/documents/cleanup/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{}")
                .header("X-Tenant-Id", testTenantId)
        ).andExpect(status().isConflict)
    }

    @Test
    fun `GET cleanup job status returns job details`() {
        val result = mockMvc.perform(
            post("/internal/documents/cleanup/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{}")
                .header("X-Tenant-Id", testTenantId)
        ).andExpect(status().isAccepted).andReturn()

        val jobId = objectMapper.readTree(result.response.contentAsString)["jobId"].asText()

        mockMvc.perform(
            get("/internal/documents/cleanup/jobs/$jobId")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.jobId").value(jobId))
    }

    @Test
    fun `DELETE cleanup job cancels active job`() {
        val result = mockMvc.perform(
            post("/internal/documents/cleanup/jobs")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{}")
                .header("X-Tenant-Id", testTenantId)
        ).andExpect(status().isAccepted).andReturn()

        val jobId = objectMapper.readTree(result.response.contentAsString)["jobId"].asText()

        mockMvc.perform(
            delete("/internal/documents/cleanup/jobs/$jobId")
                .header("X-Tenant-Id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.cancelled").value(true))
    }
}
