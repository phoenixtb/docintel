package com.docintel.admin.service

import com.docintel.admin.dto.CacheStats
import com.docintel.admin.dto.TenantQueryStats
import com.fasterxml.jackson.databind.ObjectMapper
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.jdbc.core.JdbcTemplate
import java.time.Instant
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

class StatsServiceTest {

    private lateinit var jdbcTemplate: JdbcTemplate
    private lateinit var cacheService: CacheService
    private lateinit var analyticsServiceClient: AnalyticsServiceClient
    private lateinit var statsService: StatsService

    @BeforeEach
    fun setUp() {
        jdbcTemplate = mockk()
        cacheService = mockk()
        analyticsServiceClient = mockk()
        statsService = StatsService(jdbcTemplate, cacheService, analyticsServiceClient, ObjectMapper())

        every { cacheService.getCacheStats() } returns CacheStats(
            totalEntries = 50,
            hitRate = 0.8,
            avgLatencySavedMs = 50,
            oldestEntry = Instant.now().minusSeconds(3600),
            newestEntry = Instant.now()
        )
    }

    @Test
    fun `getSystemStats returns correct counts from documents schema`() {
        every {
            jdbcTemplate.queryForObject(match { it.contains("documents.documents") && !it.contains("WHERE") && it.contains("COUNT") }, Long::class.java)
        } returns 100L

        every {
            jdbcTemplate.queryForObject(match { it.contains("documents.documents") && it.contains("SUM") }, Long::class.java)
        } returns 500L

        every {
            jdbcTemplate.queryForObject(match { it.contains("tenants") && it.contains("COUNT") }, Long::class.java)
        } returns 10L

        val stats = statsService.getSystemStats()

        assertEquals(100L, stats.totalDocuments)
        assertEquals(500L, stats.totalChunks)
        assertEquals(0L, stats.totalQueries)  // always 0 — analytics-service is source of truth
        assertEquals(10L, stats.totalTenants)
        assertNotNull(stats.cacheStats)
    }

    @Test
    fun `getSystemStats handles null values from database`() {
        every { jdbcTemplate.queryForObject(any(), Long::class.java) } returns null

        val stats = statsService.getSystemStats()

        assertEquals(0L, stats.totalDocuments)
        assertEquals(0L, stats.totalChunks)
        assertEquals(0L, stats.totalTenants)
    }

    @Test
    fun `getTenantUsage delegates query stats to analyticsServiceClient`() {
        val tenantId = "test-tenant"

        every {
            jdbcTemplate.queryForObject(
                match { it.contains("documents.documents") && it.contains("COUNT") && it.contains("WHERE") },
                Long::class.java, tenantId
            )
        } returns 10L

        every {
            jdbcTemplate.queryForObject(
                match { it.contains("documents.documents") && it.contains("SUM") && it.contains("WHERE") },
                Long::class.java, tenantId
            )
        } returns 50L

        every { analyticsServiceClient.getTenantStats(tenantId) } returns TenantQueryStats(
            totalQueries = 100L,
            queriesLast24h = 20L,
            cacheHitRate = 0.25
        )

        val usage = statsService.getTenantUsage(tenantId)

        assertNotNull(usage)
        assertEquals(tenantId, usage.tenantId)
        assertEquals(10, usage.documentCount)
        assertEquals(50, usage.chunkCount)
        assertEquals(100L, usage.totalQueries)
        assertEquals(20L, usage.queriesLast24h)
        assertEquals(0.25, usage.cacheHitRate, 0.01)

        verify(exactly = 1) { analyticsServiceClient.getTenantStats(tenantId) }
    }

    @Test
    fun `getTenantUsage returns zeros when analytics-service returns defaults`() {
        val tenantId = "empty-tenant"

        every { jdbcTemplate.queryForObject(any(), Long::class.java, tenantId) } returns 0L
        every { analyticsServiceClient.getTenantStats(tenantId) } returns TenantQueryStats()

        val usage = statsService.getTenantUsage(tenantId)

        assertNotNull(usage)
        assertEquals(0.0, usage.cacheHitRate)
        assertEquals(0L, usage.totalQueries)
    }

    @Test
    fun `listTenants returns tenant summaries without query_log`() {
        every {
            jdbcTemplate.query(any<String>(), any<org.springframework.jdbc.core.RowMapper<*>>())
        } returns listOf(
            com.docintel.admin.dto.TenantSummary(
                tenantId = "tenant-1",
                name = "Tenant One",
                documentCount = 10,
                queryCount = 0L
            ),
            com.docintel.admin.dto.TenantSummary(
                tenantId = "tenant-2",
                name = "Tenant Two",
                documentCount = 5,
                queryCount = 0L
            )
        )

        val tenants = statsService.listTenants()

        assertEquals(2, tenants.size)
        assertEquals("tenant-1", tenants[0].tenantId)
        assertEquals(10, tenants[0].documentCount)
    }

    @Test
    fun `listTenants returns empty list when no tenants`() {
        every {
            jdbcTemplate.query(any<String>(), any<org.springframework.jdbc.core.RowMapper<*>>())
        } returns emptyList<Any>()

        assertTrue(statsService.listTenants().isEmpty())
    }
}
