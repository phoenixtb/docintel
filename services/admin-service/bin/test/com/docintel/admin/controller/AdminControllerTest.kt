package com.docintel.admin.controller

import com.docintel.admin.BaseIntegrationTest
import com.docintel.admin.dto.CacheStats
import com.docintel.admin.dto.ClearCacheResponse
import com.docintel.admin.dto.TenantQueryStats
import com.docintel.admin.service.AnalyticsServiceClient
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
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*
import org.springframework.test.web.servlet.result.MockMvcResultMatchers.*
import java.time.Instant
import java.util.UUID

private const val TEST_SECRET = "test-internal-secret"

@AutoConfigureMockMvc
class AdminControllerTest : BaseIntegrationTest() {

    @Autowired private lateinit var mockMvc: MockMvc
    @Autowired private lateinit var objectMapper: ObjectMapper
    @Autowired private lateinit var jdbcTemplate: JdbcTemplate

    @MockkBean private lateinit var cacheService: CacheService
    @MockkBean private lateinit var analyticsServiceClient: AnalyticsServiceClient

    @BeforeEach
    fun setUp() {
        jdbcTemplate.execute("DELETE FROM documents.documents")
        jdbcTemplate.execute("DELETE FROM admin.tenants")

        every { cacheService.getCacheStats() } returns CacheStats(
            totalEntries = 100,
            hitRate = 0.75,
            avgLatencySavedMs = 50,
            oldestEntry = Instant.now().minusSeconds(3600),
            newestEntry = Instant.now()
        )
        every { cacheService.clearAllCache() } returns ClearCacheResponse(
            success = true, entriesCleared = 100, tenantId = null
        )
        every { cacheService.clearTenantCache(any()) } answers {
            ClearCacheResponse(success = true, entriesCleared = 10, tenantId = firstArg())
        }
        every { analyticsServiceClient.getTenantStats(any()) } returns TenantQueryStats(
            totalQueries = 3L, queriesLast24h = 1L, cacheHitRate = 0.0
        )
    }

    // All test endpoints are /internal/**, which InternalAuthFilter validates via HMAC.
    // Token = HMAC(":tenantId:", secret) matching the service-token convention.
    private fun internalToken(tenantId: String = "") =
        com.docintel.admin.filter.HmacUtils.compute(":$tenantId:", TEST_SECRET)

    private fun get(url: String, tenantId: String = "") =
        MockMvcRequestBuilders.get(url)
            .header("X-Internal-Service-Token", internalToken(tenantId))
            .apply { if (tenantId.isNotBlank()) header("X-Tenant-Id", tenantId) }

    private fun post(url: String, tenantId: String = "") =
        MockMvcRequestBuilders.post(url)
            .header("X-Internal-Service-Token", internalToken(tenantId))
            .apply { if (tenantId.isNotBlank()) header("X-Tenant-Id", tenantId) }

    @Test
    fun `GET health should return system health status`() {
        mockMvc.perform(get("/internal/health"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.status").exists())
    }

    @Test
    fun `GET stats should return correct counts`() {
        insertTestData()

        mockMvc.perform(get("/internal/stats"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.totalDocuments").value(3))
            .andExpect(jsonPath("$.totalChunks").value(6))   // SUM(chunk_count) = 2+2+2
            .andExpect(jsonPath("$.totalQueries").value(0))  // always 0 at system level
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
        insertTestData()

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
    fun `GET tenant usage delegates query stats to analytics-service`() {
        insertTestData()

        mockMvc.perform(get("/internal/tenants/tenant-1/usage"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.tenantId").value("tenant-1"))
            .andExpect(jsonPath("$.documentCount").value(2))
            .andExpect(jsonPath("$.chunkCount").value(4))  // SUM(chunk_count) for tenant-1
            .andExpect(jsonPath("$.totalQueries").value(3))
    }

    @Test
    fun `GET tenant usage for non-existent tenant should return zeros`() {
        mockMvc.perform(get("/internal/tenants/non-existent/usage"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.documentCount").value(0))
            .andExpect(jsonPath("$.totalQueries").value(3))  // from mocked analytics client
    }

    @Test
    fun `GET tenant usage includes cacheHitRate from analytics-service`() {
        insertTestData()
        every { analyticsServiceClient.getTenantStats("tenant-1") } returns TenantQueryStats(
            totalQueries = 10L, queriesLast24h = 5L, cacheHitRate = 0.5
        )

        mockMvc.perform(get("/internal/tenants/tenant-1/usage"))
            .andExpect(status().isOk)
            .andExpect(jsonPath("$.cacheHitRate").value(0.5))
    }

    private fun insertTestData() {
        jdbcTemplate.update("INSERT INTO admin.tenants (id, name) VALUES (?, ?)", "tenant-1", "Tenant One")
        jdbcTemplate.update("INSERT INTO admin.tenants (id, name) VALUES (?, ?)", "tenant-2", "Tenant Two")

        // chunk_count stored directly in documents.documents
        jdbcTemplate.update(
            "INSERT INTO documents.documents (id, tenant_id, filename, status, chunk_count) VALUES (?, ?, ?, ?, ?)",
            UUID.randomUUID(), "tenant-1", "doc1.txt", "COMPLETED", 2
        )
        jdbcTemplate.update(
            "INSERT INTO documents.documents (id, tenant_id, filename, status, chunk_count) VALUES (?, ?, ?, ?, ?)",
            UUID.randomUUID(), "tenant-1", "doc2.txt", "COMPLETED", 2
        )
        jdbcTemplate.update(
            "INSERT INTO documents.documents (id, tenant_id, filename, status, chunk_count) VALUES (?, ?, ?, ?, ?)",
            UUID.randomUUID(), "tenant-2", "doc3.txt", "PENDING", 2
        )
    }
}
