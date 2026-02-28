package com.docintel.admin.dto

import java.time.Instant

enum class HealthStatus {
    UP, DOWN, DEGRADED
}

data class ComponentHealth(
    val name: String,
    val status: HealthStatus,
    val latencyMs: Long? = null,
    val message: String? = null
)

data class SystemHealth(
    val status: HealthStatus,
    val components: Map<String, ComponentHealth>,
    val timestamp: Instant = Instant.now()
)

data class TenantSummary(
    val tenantId: String,
    val name: String,
    val documentCount: Int,
    val queryCount: Long
)

data class TenantUsage(
    val tenantId: String,
    val documentCount: Int,
    val chunkCount: Int,
    val totalQueries: Long,
    val queriesLast24h: Long,
    val cacheHitRate: Double,
    val storageBytes: Long,
    val lastQueryAt: Instant?
)

data class CacheStats(
    val totalEntries: Long,
    val hitRate: Double,
    val avgLatencySavedMs: Long,
    val oldestEntry: Instant?,
    val newestEntry: Instant?
)

data class SystemStats(
    val totalDocuments: Long,
    val totalChunks: Long,
    val totalQueries: Long,
    val totalTenants: Long,
    val cacheStats: CacheStats?
)

data class ClearCacheResponse(
    val success: Boolean,
    val entriesCleared: Long,
    val tenantId: String?
)

// ---- Tenant Management DTOs ----

data class CreateTenantRequest(
    val id: String,
    val name: String,
    val quotaDocuments: Int = 1000,
    val quotaQueriesPerDay: Int = 10000
)

data class UpdateTenantRequest(
    val name: String?,
    val quotaDocuments: Int?,
    val quotaQueriesPerDay: Int?
)

data class DeleteTenantResponse(
    val success: Boolean,
    val tenantId: String
)

// ---- User Management DTOs ----

data class TenantUser(
    val id: String,
    val email: String,
    val username: String,
    val name: String,
    val role: String,
    val tenantId: String
)

data class UpdateUserRoleRequest(
    val role: String
)
