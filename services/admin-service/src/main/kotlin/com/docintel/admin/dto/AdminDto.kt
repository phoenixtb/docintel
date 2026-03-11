package com.docintel.admin.dto

import jakarta.validation.constraints.NotBlank
import jakarta.validation.constraints.Pattern
import jakarta.validation.constraints.Size
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
    val queryCount: Long,
    val quotaDocuments: Int = 1000,
    val quotaQueriesPerDay: Int = 10000,
    val settings: Map<String, Any?> = emptyMap()
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
    @field:NotBlank @field:Size(min = 2, max = 64)
    @field:Pattern(regexp = "^[a-z0-9][a-z0-9_-]*$", message = "id must be lowercase alphanumeric with _ or -")
    val id: String,
    @field:NotBlank @field:Size(min = 1, max = 200)
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

// ---- Model / Platform Settings DTOs ----

data class PlatformSettings(
    val llmModel: String?          // null = "Tenant Choice"
)

data class UpdatePlatformSettingsRequest(
    val llmModel: String?          // null = revert to "Tenant Choice"
)

data class UpdateTenantSettingsRequest(
    val llmModel: String?          // null = clear preference (use platform default)
)

data class TenantSettings(
    val llmModel: String?,         // null = using platform default / no preference set
    val effectiveModel: String?    // what actually resolves (platform override or own pref)
)
