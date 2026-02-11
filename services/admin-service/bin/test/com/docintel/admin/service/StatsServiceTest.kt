package com.docintel.admin.service

import com.docintel.admin.dto.CacheStats
import io.mockk.every
import io.mockk.mockk
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.jdbc.core.JdbcTemplate
import java.time.Instant
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Unit tests for StatsService.
 */
class StatsServiceTest {

    private lateinit var jdbcTemplate: JdbcTemplate
    private lateinit var cacheService: CacheService
    private lateinit var statsService: StatsService

    @BeforeEach
    fun setUp() {
        jdbcTemplate = mockk()
        cacheService = mockk()
        statsService = StatsService(jdbcTemplate, cacheService)

        // Default cache stats mock with correct field names
        every { cacheService.getCacheStats() } returns CacheStats(
            totalEntries = 50,
            hitRate = 0.8,
            avgLatencySavedMs = 50,
            oldestEntry = Instant.now().minusSeconds(3600),
            newestEntry = Instant.now()
        )
    }

    @Test
    fun `getSystemStats should return correct counts`() {
        // Given
        every { jdbcTemplate.queryForObject(match { it.contains("documents") }, Long::class.java) } returns 100L
        every { jdbcTemplate.queryForObject(match { it.contains("chunks") }, Long::class.java) } returns 500L
        every { jdbcTemplate.queryForObject(match { it.contains("query_log") }, Long::class.java) } returns 1000L
        every { jdbcTemplate.queryForObject(match { it.contains("tenants") }, Long::class.java) } returns 10L

        // When
        val stats = statsService.getSystemStats()

        // Then
        assertEquals(100L, stats.totalDocuments)
        assertEquals(500L, stats.totalChunks)
        assertEquals(1000L, stats.totalQueries)
        assertEquals(10L, stats.totalTenants)
        assertNotNull(stats.cacheStats)
    }

    @Test
    fun `getSystemStats should handle null values from database`() {
        // Given
        every { jdbcTemplate.queryForObject(any(), Long::class.java) } returns null

        // When
        val stats = statsService.getSystemStats()

        // Then
        assertEquals(0L, stats.totalDocuments)
        assertEquals(0L, stats.totalChunks)
        assertEquals(0L, stats.totalQueries)
        assertEquals(0L, stats.totalTenants)
    }

    @Test
    fun `getTenantUsage should return correct usage statistics`() {
        // Given
        val tenantId = "test-tenant"
        
        every { 
            jdbcTemplate.queryForObject(
                match { it.contains("documents") && it.contains("tenant_id") },
                Long::class.java,
                tenantId
            )
        } returns 10L
        
        every { 
            jdbcTemplate.queryForObject(
                match { it.contains("chunks") && it.contains("tenant_id") },
                Long::class.java,
                tenantId
            )
        } returns 50L
        
        every { 
            jdbcTemplate.queryForObject(
                match { it.contains("query_log") && !it.contains("24 hours") && !it.contains("cached") },
                Long::class.java,
                tenantId
            )
        } returns 100L
        
        every { 
            jdbcTemplate.queryForObject(
                match { it.contains("24 hours") },
                Long::class.java,
                tenantId
            )
        } returns 20L
        
        every { 
            jdbcTemplate.queryForObject(
                match { it.contains("cached = true") },
                Long::class.java,
                tenantId
            )
        } returns 25L
        
        every { 
            jdbcTemplate.queryForObject(
                match { it.contains("MAX") },
                java.sql.Timestamp::class.java,
                tenantId
            )
        } returns null

        // When
        val usage = statsService.getTenantUsage(tenantId)

        // Then
        assertNotNull(usage)
        assertEquals(tenantId, usage.tenantId)
        assertEquals(10, usage.documentCount)
        assertEquals(50, usage.chunkCount)
        assertEquals(100L, usage.totalQueries)
        assertEquals(20L, usage.queriesLast24h)
        assertEquals(0.25, usage.cacheHitRate, 0.01) // 25/100
    }

    @Test
    fun `getTenantUsage should calculate zero cache hit rate for no queries`() {
        // Given
        val tenantId = "empty-tenant"
        
        every { jdbcTemplate.queryForObject(any(), Long::class.java, tenantId) } returns 0L
        every { jdbcTemplate.queryForObject(any(), java.sql.Timestamp::class.java, tenantId) } returns null

        // When
        val usage = statsService.getTenantUsage(tenantId)

        // Then
        assertNotNull(usage)
        assertEquals(0.0, usage.cacheHitRate)
    }

    @Test
    fun `listTenants should return tenant summaries`() {
        // Given
        every { 
            jdbcTemplate.query(any<String>(), any<org.springframework.jdbc.core.RowMapper<*>>())
        } returns listOf(
            com.docintel.admin.dto.TenantSummary(
                tenantId = "tenant-1",
                name = "Tenant One",
                documentCount = 10,
                queryCount = 100
            ),
            com.docintel.admin.dto.TenantSummary(
                tenantId = "tenant-2",
                name = "Tenant Two",
                documentCount = 5,
                queryCount = 50
            )
        )

        // When
        val tenants = statsService.listTenants()

        // Then
        assertEquals(2, tenants.size)
        assertEquals("tenant-1", tenants[0].tenantId)
        assertEquals("Tenant One", tenants[0].name)
        assertEquals(10, tenants[0].documentCount)
    }

    @Test
    fun `listTenants should return empty list for no tenants`() {
        // Given
        every { 
            jdbcTemplate.query(any<String>(), any<org.springframework.jdbc.core.RowMapper<*>>())
        } returns emptyList<Any>()

        // When
        val tenants = statsService.listTenants()

        // Then
        assertTrue(tenants.isEmpty())
    }
}
