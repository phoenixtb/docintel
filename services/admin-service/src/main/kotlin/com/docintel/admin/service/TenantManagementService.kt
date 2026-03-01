package com.docintel.admin.service

import com.docintel.admin.dto.*
import org.slf4j.LoggerFactory
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

@Service
class TenantManagementService(
    private val jdbcTemplate: JdbcTemplate,
    private val authentikService: AuthentikService,
    private val cacheService: CacheService,
    private val provisioningService: ProvisioningService,
) {
    private val log = LoggerFactory.getLogger(TenantManagementService::class.java)

    @Transactional
    fun createTenant(req: CreateTenantRequest): TenantSummary {
        jdbcTemplate.update(
            """
            INSERT INTO tenants (id, name, quota_documents, quota_queries_per_day)
            VALUES (?, ?, ?, ?)
            """.trimIndent(),
            req.id, req.name, req.quotaDocuments, req.quotaQueriesPerDay
        )
        authentikService.createTenantGroup(req.id, "tenant_user")
        // Provision per-tenant Qdrant collection and MinIO bucket
        provisioningService.createQdrantCollection(req.id)
        provisioningService.createMinioBucket(req.id)
        log.info("Created tenant ${req.id}")
        return TenantSummary(tenantId = req.id, name = req.name, documentCount = 0, queryCount = 0)
    }

    @Transactional
    fun updateTenant(tenantId: String, req: UpdateTenantRequest): TenantSummary? {
        val updates = mutableListOf<String>()
        val params = mutableListOf<Any>()

        req.name?.let { updates.add("name = ?"); params.add(it) }
        req.quotaDocuments?.let { updates.add("quota_documents = ?"); params.add(it) }
        req.quotaQueriesPerDay?.let { updates.add("quota_queries_per_day = ?"); params.add(it) }

        if (updates.isEmpty()) return getTenantSummary(tenantId)

        params.add(tenantId)
        val affected = jdbcTemplate.update(
            "UPDATE tenants SET ${updates.joinToString(", ")}, updated_at = NOW() WHERE id = ?",
            *params.toTypedArray()
        )
        return if (affected > 0) getTenantSummary(tenantId) else null
    }

    @Transactional
    fun deleteTenant(tenantId: String): DeleteTenantResponse {
        // Cascade: chunks, query_log, documents, conversations all FK to tenants with ON DELETE CASCADE
        // or we delete manually to be safe
        jdbcTemplate.update("DELETE FROM query_log WHERE tenant_id = ?", tenantId)
        jdbcTemplate.update("DELETE FROM chunks WHERE tenant_id = ?", tenantId)
        jdbcTemplate.update("DELETE FROM documents WHERE tenant_id = ?", tenantId)
        jdbcTemplate.update("DELETE FROM conversations WHERE tenant_id = ?", tenantId)
        jdbcTemplate.update("DELETE FROM users WHERE tenant_id = ?", tenantId)
        val affected = jdbcTemplate.update("DELETE FROM tenants WHERE id = ?", tenantId)

        cacheService.clearTenantCache(tenantId)
        authentikService.deleteTenantGroup(tenantId)
        // Deprovision per-tenant Qdrant collection and MinIO bucket
        provisioningService.deleteQdrantCollection(tenantId)
        provisioningService.deleteMinioBucket(tenantId)

        log.info("Deleted tenant $tenantId")
        return DeleteTenantResponse(success = affected > 0, tenantId = tenantId)
    }

    fun getTenantUsers(tenantId: String): List<TenantUser> {
        return authentikService.getUsersByTenant(tenantId)
    }

    fun updateUserRole(tenantId: String, userId: String, req: UpdateUserRoleRequest): Boolean {
        val allowedRoles = setOf("platform_admin", "tenant_admin", "tenant_user")
        if (req.role !in allowedRoles) return false
        authentikService.updateUserRole(userId, tenantId, req.role)
        return true
    }

    private fun getTenantSummary(tenantId: String): TenantSummary? {
        return try {
            jdbcTemplate.queryForObject(
                """
                SELECT t.id, t.name,
                       COALESCE(d.doc_count, 0) as doc_count,
                       COALESCE(q.query_count, 0) as query_count
                FROM tenants t
                LEFT JOIN (SELECT tenant_id, COUNT(*) as doc_count FROM documents GROUP BY tenant_id) d ON t.id = d.tenant_id
                LEFT JOIN (SELECT tenant_id, COUNT(*) as query_count FROM query_log GROUP BY tenant_id) q ON t.id = q.tenant_id
                WHERE t.id = ?
                """.trimIndent(),
                { rs, _ -> TenantSummary(rs.getString("id"), rs.getString("name"), rs.getInt("doc_count"), rs.getLong("query_count")) },
                tenantId
            )
        } catch (e: Exception) {
            null
        }
    }
}
