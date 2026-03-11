package com.docintel.admin.service

import com.docintel.admin.dto.SystemStats
import com.docintel.admin.dto.TenantSummary
import com.docintel.admin.dto.TenantUsage
import com.fasterxml.jackson.databind.ObjectMapper
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import java.time.Instant

@Service
class StatsService(
    private val jdbcTemplate: JdbcTemplate,
    private val cacheService: CacheService,
    private val objectMapper: ObjectMapper,
) {
    fun getSystemStats(): SystemStats {
        val documentCount = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM documents",
            Long::class.java
        ) ?: 0L

        val chunkCount = jdbcTemplate.queryForObject(
            "SELECT COALESCE(SUM(chunk_count), 0) FROM documents",
            Long::class.java
        ) ?: 0L

        val queryCount = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM query_log",
            Long::class.java
        ) ?: 0L

        val tenantCount = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM tenants",
            Long::class.java
        ) ?: 0L

        return SystemStats(
            totalDocuments = documentCount,
            totalChunks = chunkCount,
            totalQueries = queryCount,
            totalTenants = tenantCount,
            cacheStats = cacheService.getCacheStats()
        )
    }

    fun listTenants(): List<TenantSummary> {
        return jdbcTemplate.query(
            """
            SELECT t.id, t.name, t.quota_documents, t.quota_queries_per_day, t.settings,
                   COALESCE(d.doc_count, 0) as doc_count,
                   COALESCE(q.query_count, 0) as query_count
            FROM tenants t
            LEFT JOIN (
                SELECT tenant_id, COUNT(*) as doc_count 
                FROM documents 
                GROUP BY tenant_id
            ) d ON t.id = d.tenant_id
            LEFT JOIN (
                SELECT tenant_id, COUNT(*) as query_count 
                FROM query_log 
                GROUP BY tenant_id
            ) q ON t.id = q.tenant_id
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
                tenantId = rs.getString("id"),
                name = rs.getString("name"),
                documentCount = rs.getInt("doc_count"),
                queryCount = rs.getLong("query_count"),
                quotaDocuments = rs.getInt("quota_documents"),
                quotaQueriesPerDay = rs.getInt("quota_queries_per_day"),
                settings = settingsJson
            )
        }
    }

    @Transactional(readOnly = true)
    fun getTenantUsage(tenantId: String): TenantUsage? {
        val documentCount = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM documents WHERE tenant_id = ?",
            Long::class.java,
            tenantId
        )?.toInt() ?: 0

        val chunkCount = jdbcTemplate.queryForObject(
            "SELECT COALESCE(SUM(chunk_count), 0) FROM documents WHERE tenant_id = ?",
            Long::class.java,
            tenantId
        )?.toInt() ?: 0

        val totalQueries = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM query_log WHERE tenant_id = ?",
            Long::class.java,
            tenantId
        ) ?: 0L

        val queriesLast24h = jdbcTemplate.queryForObject(
            """
            SELECT COUNT(*) FROM query_log 
            WHERE tenant_id = ? 
            AND created_at > NOW() - INTERVAL '24 hours'
            """.trimIndent(),
            Long::class.java,
            tenantId
        ) ?: 0L

        val cachedQueries = jdbcTemplate.queryForObject(
            "SELECT COUNT(*) FROM query_log WHERE tenant_id = ? AND cached = true",
            Long::class.java,
            tenantId
        ) ?: 0L

        val cacheHitRate = if (totalQueries > 0) {
            cachedQueries.toDouble() / totalQueries
        } else {
            0.0
        }

        val lastQueryAt = jdbcTemplate.queryForObject(
            "SELECT MAX(created_at) FROM query_log WHERE tenant_id = ?",
            java.sql.Timestamp::class.java,
            tenantId
        )?.toInstant()

        return TenantUsage(
            tenantId = tenantId,
            documentCount = documentCount,
            chunkCount = chunkCount,
            totalQueries = totalQueries,
            queriesLast24h = queriesLast24h,
            cacheHitRate = cacheHitRate,
            storageBytes = 0, // Would need MinIO integration
            lastQueryAt = lastQueryAt
        )
    }
}
