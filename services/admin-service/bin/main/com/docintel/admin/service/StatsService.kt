package com.docintel.admin.service

import com.docintel.admin.dto.SystemStats
import com.docintel.admin.dto.TenantSummary
import com.docintel.admin.dto.TenantUsage
import com.fasterxml.jackson.databind.ObjectMapper
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.time.Instant

/**
 * Tenant and system statistics.
 *
 * Document/chunk counts are read from documents.documents (cross-schema read;
 * docintel_admin has SELECT grant on documents.documents).
 * Query stats are sourced from analytics-service (ClickHouse) via AnalyticsServiceClient.
 * The dead query_log table has been dropped.
 */
@Service
class StatsService(
    private val jdbcTemplate: JdbcTemplate,
    private val cacheService: CacheService,
    private val analyticsServiceClient: AnalyticsServiceClient,
    private val objectMapper: ObjectMapper,
) {
    fun getSystemStats(): SystemStats {
        val documentCount = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM documents.documents",
            Long::class.java
        ) ?: 0L

        val chunkCount = jdbcTemplate.queryForObject(
            "SELECT COALESCE(SUM(chunk_count), 0) FROM documents.documents",
            Long::class.java
        ) ?: 0L

        val tenantCount = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM tenants",
            Long::class.java
        ) ?: 0L

        return SystemStats(
            totalDocuments = documentCount,
            totalChunks    = chunkCount,
            totalQueries   = 0L,
            totalTenants   = tenantCount,
            cacheStats     = cacheService.getCacheStats()
        )
    }

    fun listTenants(): List<TenantSummary> {
        return jdbcTemplate.query(
            """
            SELECT t.id, t.name, t.quota_documents, t.quota_queries_per_day, t.settings,
                   COALESCE(d.doc_count, 0) as doc_count
            FROM tenants t
            LEFT JOIN (
                SELECT tenant_id, COUNT(*) as doc_count
                FROM documents.documents
                GROUP BY tenant_id
            ) d ON t.id = d.tenant_id
            ORDER BY t.name
            """.trimIndent()
        ) { rs, _ ->
            @Suppress("UNCHECKED_CAST")
            val settingsJson = runCatching {
                rs.getString("settings")?.let {
                    objectMapper.readValue(it, Map::class.java) as Map<String, Any?>
                } ?: emptyMap()
            }.getOrDefault(emptyMap())
            TenantSummary(
                tenantId          = rs.getString("id"),
                name              = rs.getString("name"),
                documentCount     = rs.getInt("doc_count"),
                queryCount        = 0L,
                quotaDocuments    = rs.getInt("quota_documents"),
                quotaQueriesPerDay = rs.getInt("quota_queries_per_day"),
                settings          = settingsJson
            )
        }
    }

    @Transactional(readOnly = true)
    fun getTenantUsage(tenantId: String): TenantUsage? {
        val documentCount = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM documents.documents WHERE tenant_id = ?",
            Long::class.java,
            tenantId
        )?.toInt() ?: 0

        val chunkCount = jdbcTemplate.queryForObject(
            "SELECT COALESCE(SUM(chunk_count), 0) FROM documents.documents WHERE tenant_id = ?",
            Long::class.java,
            tenantId
        )?.toInt() ?: 0

        val queryStats = analyticsServiceClient.getTenantStats(tenantId)

        return TenantUsage(
            tenantId       = tenantId,
            documentCount  = documentCount,
            chunkCount     = chunkCount,
            totalQueries   = queryStats.totalQueries,
            queriesLast24h = queryStats.queriesLast24h,
            cacheHitRate   = queryStats.cacheHitRate,
            storageBytes   = 0,
            lastQueryAt    = null
        )
    }
}
