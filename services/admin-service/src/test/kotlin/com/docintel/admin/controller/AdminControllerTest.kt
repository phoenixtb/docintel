package com.docintel.admin.controller

import com.docintel.admin.BaseIntegrationTest
import com.docintel.admin.dto.CacheStats
import com.docintel.admin.dto.ClearCacheResponse
import com.docintel.admin.service.CacheService
import com.fasterxml.jackson.databind.ObjectMapper
import com.ninjasquad.springmockk.MockkBean
import io.mockk.every
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.test.web.servlet.MockMvc
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.*
import java.time.Instant
import java.util.UUID

/**
 * Integration tests for AdminController.
 */
@AutoConfigureMockMvc
class AdminControllerTest : BaseIntegrationTest() {

    @Autowired
    private lateinit var mockMvc: MockMvc

    @Autowired
    private lateinit var objectMapper: ObjectMapper

    @Autowired
    private lateinit var jdbcTemplate: JdbcTemplate

    @MockkBean
    private lateinit var cacheService: CacheService

    @BeforeEach
    fun setUp() {
        // Clean up test data
        jdbcTemplate.execute("DELETE FROM query_log")
        jdbcTemplate.execute("DELETE FROM chunks")
        jdbcTemplate.execute("DELETE FROM documents")
        jdbcTemplate.execute("DELETE FROM tenants")

        // Mock cache service responses with correct field names
        every { cacheService.getCacheStats() } returns CacheStats(
            totalEntries = 100,
            hitRate = 0.75,
            avgLatencySavedMs = 50,
            oldestEntry = Instant.now().minusSeconds(3600),
            newestEntry = Instant.now()
        )
        
        every { cacheService.clearAllCache() } returns ClearCacheResponse(
            success = true,
            entriesCleared = 100,
            tenantId = null
        )
        
        every { cacheService.clearTenantCache(any()) } answers {
            ClearCacheResponse(
                success = true,
                entriesCleared = 10,
                tenantId = firstArg()
            )
        }
    }

    @Test
    fun `GET health should return system health status`() {
        mockMvc.perform(get("/internal/health"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.status").exists())
    }

    @Test
    fun `GET stats should return system statistics`() {
        // Given - Insert some test data
        insertTestData()

        // When & Then
        mockMvc.perform(get("/internal/stats"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.totalDocuments").value(3))
            .andExpect(jsonPath("$.totalChunks").value(6))
            .andExpect(jsonPath("$.totalQueries").value(5))
    }

    @Test
    fun `GET stats should return zero counts for empty database`() {
        mockMvc.perform(get("/internal/stats"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.totalDocuments").value(0))
            .andExpect(jsonPath("$.totalChunks").value(0))
            .andExpect(jsonPath("$.totalQueries").value(0))
    }

    @Test
    fun `GET cache stats should return cache statistics`() {
        mockMvc.perform(get("/internal/cache/stats"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.totalEntries").value(100))
            .andExpect(jsonPath("$.hitRate").value(0.75))
    }

    @Test
    fun `POST cache clear should clear all cache`() {
        mockMvc.perform(post("/internal/cache/clear"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.success").value(true))
            .andExpect(jsonPath("$.entriesCleared").value(100))
    }

    @Test
    fun `POST cache clear tenant should clear tenant cache`() {
        mockMvc.perform(post("/internal/cache/clear/tenant-1"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.success").value(true))
            .andExpect(jsonPath("$.entriesCleared").value(10))
    }

    @Test
    fun `GET tenants should return list of tenants`() {
        // Given
        insertTestData()

        // When & Then
        mockMvc.perform(get("/internal/tenants"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$").isArray)
            .andExpect(jsonPath("$.length()").value(2))
    }

    @Test
    fun `GET tenants should return empty list for no tenants`() {
        mockMvc.perform(get("/internal/tenants"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$").isEmpty)
    }

    @Test
    fun `GET tenant usage should return usage statistics`() {
        // Given
        insertTestData()

        // When & Then
        mockMvc.perform(get("/internal/tenants/tenant-1/usage"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.tenantId").value("tenant-1"))
            .andExpect(jsonPath("$.documentCount").value(2))
            .andExpect(jsonPath("$.chunkCount").value(4))
            .andExpect(jsonPath("$.totalQueries").value(3))
    }

    @Test
    fun `GET tenant usage for non-existent tenant should return zeros`() {
        mockMvc.perform(get("/internal/tenants/non-existent/usage"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.documentCount").value(0))
            .andExpect(jsonPath("$.totalQueries").value(0))
    }

    @Test
    fun `GET tenant usage should include cache hit rate`() {
        // Given
        insertTestData()
        // Add cached query
        jdbcTemplate.update(
            "INSERT INTO query_log (tenant_id, query, cached) VALUES (?, ?, ?)",
            "tenant-1", "cached query", true
        )

        // When & Then
        mockMvc.perform(get("/internal/tenants/tenant-1/usage"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.cacheHitRate").exists())
    }

    // Helper to insert test data
    private fun insertTestData() {
        // Insert tenants
        jdbcTemplate.update("INSERT INTO tenants (id, name) VALUES (?, ?)", "tenant-1", "Tenant One")
        jdbcTemplate.update("INSERT INTO tenants (id, name) VALUES (?, ?)", "tenant-2", "Tenant Two")

        // Insert documents
        val doc1Id = UUID.randomUUID()
        val doc2Id = UUID.randomUUID()
        val doc3Id = UUID.randomUUID()
        
        jdbcTemplate.update(
            "INSERT INTO documents (id, tenant_id, filename, status) VALUES (?, ?, ?, ?)",
            doc1Id, "tenant-1", "doc1.txt", "COMPLETED"
        )
        jdbcTemplate.update(
            "INSERT INTO documents (id, tenant_id, filename, status) VALUES (?, ?, ?, ?)",
            doc2Id, "tenant-1", "doc2.txt", "COMPLETED"
        )
        jdbcTemplate.update(
            "INSERT INTO documents (id, tenant_id, filename, status) VALUES (?, ?, ?, ?)",
            doc3Id, "tenant-2", "doc3.txt", "PENDING"
        )

        // Insert chunks
        repeat(2) { i ->
            jdbcTemplate.update(
                "INSERT INTO chunks (id, document_id, tenant_id, content, chunk_index) VALUES (?, ?, ?, ?, ?)",
                UUID.randomUUID(), doc1Id, "tenant-1", "Chunk $i content", i
            )
        }
        repeat(2) { i ->
            jdbcTemplate.update(
                "INSERT INTO chunks (id, document_id, tenant_id, content, chunk_index) VALUES (?, ?, ?, ?, ?)",
                UUID.randomUUID(), doc2Id, "tenant-1", "Chunk $i content", i
            )
        }
        repeat(2) { i ->
            jdbcTemplate.update(
                "INSERT INTO chunks (id, document_id, tenant_id, content, chunk_index) VALUES (?, ?, ?, ?, ?)",
                UUID.randomUUID(), doc3Id, "tenant-2", "Chunk $i content", i
            )
        }

        // Insert query logs
        repeat(3) {
            jdbcTemplate.update(
                "INSERT INTO query_log (tenant_id, query, cached) VALUES (?, ?, ?)",
                "tenant-1", "Query $it", false
            )
        }
        repeat(2) {
            jdbcTemplate.update(
                "INSERT INTO query_log (tenant_id, query, cached) VALUES (?, ?, ?)",
                "tenant-2", "Query $it", false
            )
        }
    }
}
