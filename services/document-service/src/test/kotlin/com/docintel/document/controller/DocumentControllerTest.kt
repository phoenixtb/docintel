package com.docintel.document.controller

import com.docintel.document.BaseIntegrationTest
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.repository.ChunkRepository
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
 * Uses Testcontainers for PostgreSQL and MinIO.
 */
@AutoConfigureMockMvc
class DocumentControllerTest : BaseIntegrationTest() {

    @Autowired
    private lateinit var mockMvc: MockMvc

    @Autowired
    private lateinit var objectMapper: ObjectMapper

    @Autowired
    private lateinit var documentRepository: DocumentRepository

    @Autowired
    private lateinit var chunkRepository: ChunkRepository

    @MockkBean
    private lateinit var ingestionServiceClient: IngestionServiceClient

    private val testTenantId = "integration-test-tenant"

    @BeforeEach
    fun setUp() {
        // Clean up test data
        chunkRepository.deleteAll()
        documentRepository.deleteAll()

        // Mock ingestion service responses
        coEvery { ingestionServiceClient.triggerIngestion(any(), any(), any(), any(), any(), any(), any()) } returns
            IngestionTriggerResponse("accepted", "test-doc-id")

        coEvery { ingestionServiceClient.deleteDocumentVectors(any(), any()) } returns true
        coEvery { ingestionServiceClient.deleteTenantVectors(any()) } returns true
    }

    @Test
    fun `POST should upload document and return 201`() {
        // Given
        val file = MockMultipartFile(
            "file",
            "test-document.txt",
            "text/plain",
            "This is test content for upload.".toByteArray()
        )

        // When & Then
        mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .param("tenant_id", testTenantId)
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.filename").value("test-document.txt"))
            .andExpect(jsonPath("$.status").value("PENDING"))
            .andExpect(jsonPath("$.id").exists())
    }

    @Test
    fun `POST should upload document with domain override`() {
        // Given
        val file = MockMultipartFile(
            "file",
            "hr-policy.txt",
            "text/plain",
            "HR Policy content".toByteArray()
        )

        // When & Then
        mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .param("tenant_id", testTenantId)
                .param("domain", "hr_policy")
        )
            .andExpect(status().isCreated)
            .andExpect(jsonPath("$.filename").value("hr-policy.txt"))
    }

    @Test
    fun `POST should upload document with metadata`() {
        // Given
        val file = MockMultipartFile(
            "file",
            "metadata-doc.txt",
            "text/plain",
            "Content".toByteArray()
        )
        val metadata = """{"author": "Test Author", "version": "1.0"}"""

        // When & Then
        mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .param("tenant_id", testTenantId)
                .param("metadata", metadata)
        )
            .andExpect(status().isCreated)
    }

    @Test
    fun `GET should return document by id`() {
        // Given
        val docId = uploadTestDocument()

        // When & Then
        mockMvc.perform(
            get("/internal/documents/$docId")
                .param("tenant_id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.id").value(docId.toString()))
            .andExpect(jsonPath("$.filename").exists())
    }

    @Test
    fun `GET should return 404 for non-existent document`() {
        // Given
        val nonExistentId = UUID.randomUUID()

        // When & Then
        mockMvc.perform(
            get("/internal/documents/$nonExistentId")
                .param("tenant_id", testTenantId)
        )
            .andExpect(status().isNotFound)
    }

    @Test
    fun `GET should not return document for wrong tenant`() {
        // Given
        val docId = uploadTestDocument()

        // When & Then
        mockMvc.perform(
            get("/internal/documents/$docId")
                .param("tenant_id", "different-tenant")
        )
            .andExpect(status().isNotFound)
    }

    @Test
    fun `GET list should return paginated documents`() {
        // Given
        repeat(5) { uploadTestDocument(filename = "doc-$it.txt") }

        // When & Then
        mockMvc.perform(
            get("/internal/documents")
                .param("tenant_id", testTenantId)
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
        // Given - upload and let some process (status changes are async)
        uploadTestDocument()

        // When & Then
        mockMvc.perform(
            get("/internal/documents")
                .param("tenant_id", testTenantId)
                .param("status", "PENDING")
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.content").isArray)
    }

    @Test
    fun `GET list should return empty for tenant with no documents`() {
        // When & Then
        mockMvc.perform(
            get("/internal/documents")
                .param("tenant_id", "empty-tenant")
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.content").isEmpty)
            .andExpect(jsonPath("$.totalElements").value(0))
    }

    @Test
    fun `DELETE should remove document and return 204`() {
        // Given
        val docId = uploadTestDocument()

        // When & Then
        mockMvc.perform(
            delete("/internal/documents/$docId")
                .param("tenant_id", testTenantId)
        )
            .andExpect(status().isNoContent)

        // Verify document is gone
        mockMvc.perform(
            get("/internal/documents/$docId")
                .param("tenant_id", testTenantId)
        )
            .andExpect(status().isNotFound)
    }

    @Test
    fun `DELETE should return 404 for non-existent document`() {
        // Given
        val nonExistentId = UUID.randomUUID()

        // When & Then
        mockMvc.perform(
            delete("/internal/documents/$nonExistentId")
                .param("tenant_id", testTenantId)
        )
            .andExpect(status().isNotFound)
    }

    @Test
    fun `should upload and retrieve actual test document`() {
        // Given
        val content = javaClass.getResource("/documents/hr_policy_leave.txt")?.readText()
            ?: throw IllegalStateException("Test file not found")
        
        val file = MockMultipartFile(
            "file",
            "hr_policy_leave.txt",
            "text/plain",
            content.toByteArray()
        )

        // When - Upload
        val uploadResult = mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .param("tenant_id", testTenantId)
                .param("domain", "hr_policy")
        )
            .andExpect(status().isCreated)
            .andReturn()

        val response = objectMapper.readTree(uploadResult.response.contentAsString)
        val docId = response["id"].asText()

        // Then - Retrieve
        mockMvc.perform(
            get("/internal/documents/$docId")
                .param("tenant_id", testTenantId)
        )
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.filename").value("hr_policy_leave.txt"))
    }

    // Helper function to upload a test document
    private fun uploadTestDocument(filename: String = "test.txt"): UUID {
        val file = MockMultipartFile(
            "file",
            filename,
            "text/plain",
            "Test content for $filename".toByteArray()
        )

        val result = mockMvc.perform(
            multipart("/internal/documents")
                .file(file)
                .param("tenant_id", testTenantId)
        )
            .andExpect(status().isCreated)
            .andReturn()

        val response = objectMapper.readTree(result.response.contentAsString)
        return UUID.fromString(response["id"].asText())
    }
}
