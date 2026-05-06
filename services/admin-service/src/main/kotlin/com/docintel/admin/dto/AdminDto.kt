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

/** Query statistics sourced from analytics-service (ClickHouse). Replaces dead query_log reads. */
data class TenantQueryStats(
    val totalQueries: Long = 0L,
    val queriesLast24h: Long = 0L,
    val cacheHitRate: Double = 0.0,
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
//
// Three runtime-tunable models per kind: chat / vlm / rerank.
// Embedding model is env-only (changing it would invalidate every vector).
//
// `llmModel` keeps its name for backward compat with the older single-model
// API; semantically it is the chat model.

data class PlatformSettings(
    val llmModel: String?,         // chat   — null = "Tenant Choice"
    val llmVlmModel: String?,      // vision — null = "Tenant Choice"
    val llmRerankModel: String?,   // rerank — null = "Tenant Choice"
)

/**
 * Sparse-update: only fields explicitly present in the JSON body are touched.
 * To distinguish "not in payload" from "explicit null", we wrap each field in
 * a small Optional-like marker. Spring/Jackson treats absent JSON fields as
 * `null` on a nullable Kotlin field — to allow explicit null clears, the UI
 * sends the field as `null`. Use absent-vs-explicit pattern via a wrapper Map
 * if a future requirement demands it; for now, null in the request always
 * means "clear this override" (matches existing UpdateTenantSettings semantics).
 */
data class UpdatePlatformSettingsRequest(
    val llmModel: String? = null,
    val llmVlmModel: String? = null,
    val llmRerankModel: String? = null,
)

data class UpdateTenantSettingsRequest(
    val llmModel: String? = null,
    val llmVlmModel: String? = null,
    val llmRerankModel: String? = null,
)

data class TenantSettings(
    val llmModel: String?,                 // chat tenant pref (null = inherit)
    val llmVlmModel: String?,              // vision tenant pref
    val llmRerankModel: String?,           // rerank tenant pref
    val effectiveModel: String?,           // chat   resolved (platform > tenant > env)
    val effectiveVlmModel: String?,        // vision resolved
    val effectiveRerankModel: String?,     // rerank resolved
)

// ---- Model Profiles DTOs ----

data class ModelProfile(
    val id: String,
    val scope: String,                   // "platform" | "tenant"
    val tenantId: String?,
    val modelPattern: String,
    val displayName: String?,
    // Standard (non-thinking) params — null = inherit from next resolution level
    val temperature: Double?,
    val topP: Double?,
    val maxTokens: Int?,
    val frequencyPenalty: Double?,
    val presencePenalty: Double?,
    val repetitionPenalty: Double?,
    val topK: Int?,
    val minP: Double?,
    // Thinking-mode params — null = inherit
    val thinkingTemperature: Double?,
    val thinkingTopP: Double?,
    val thinkingMaxTokens: Int?,
    val thinkingFrequencyPenalty: Double?,
    val thinkingPresencePenalty: Double?,
    val thinkingRepetitionPenalty: Double?,
    val thinkingTopK: Int?,
    val thinkingMinP: Double?,
    val thinkingBudget: Int?,
    val streamThinking: Boolean?,
    /** Informational: chat | vlm | embed | rerank. NULL = auto-infer from modelPattern. */
    val kind: String?,
    val notes: String?,
    val createdAt: Instant,
    val updatedAt: Instant,
)

data class UpsertModelProfileRequest(
    val modelPattern: String,
    val displayName: String? = null,
    val temperature: Double? = null,
    val topP: Double? = null,
    val maxTokens: Int? = null,
    val frequencyPenalty: Double? = null,
    val presencePenalty: Double? = null,
    val repetitionPenalty: Double? = null,
    val topK: Int? = null,
    val minP: Double? = null,
    val thinkingTemperature: Double? = null,
    val thinkingTopP: Double? = null,
    val thinkingMaxTokens: Int? = null,
    val thinkingFrequencyPenalty: Double? = null,
    val thinkingPresencePenalty: Double? = null,
    val thinkingRepetitionPenalty: Double? = null,
    val thinkingTopK: Int? = null,
    val thinkingMinP: Double? = null,
    val thinkingBudget: Int? = null,
    val streamThinking: Boolean? = null,
    /** Optional. Allowed: chat | vlm | embed | rerank. NULL = auto-infer from modelPattern. */
    val kind: String? = null,
    val notes: String? = null,
)

// ---- Active Models DTOs (resolved chat/vlm/embed/rerank models in use) ----

/**
 * One row of the "Active Models" panel. `model` is the **effective** value
 * after platform/tenant/env resolution. `source` tells the UI where the value
 * came from so it can render an appropriate badge / lock icon.
 *
 * `tunable=false` means the value is locked at the env layer (currently:
 * embed model only — changing it requires rebuilding the vector store).
 *
 * `available` lists model ids the UI can offer in the dropdown for this kind.
 * Empty list = let the UI fall back to /api/v1/models.
 */
data class ActiveModelInfo(
    val model: String?,
    val kind: String,
    val source: String,                  // "platform" | "tenant" | "env" | "none"
    val envFallback: String?,            // for transparency / "Reset to env" button
    val tunable: Boolean = true,         // false = read-only (e.g. embed)
    val disabled: Boolean = false,       // true = whole feature disabled (e.g. USE_RERANKING=false)
)

data class ActiveModels(
    val chat: ActiveModelInfo,
    val vlm: ActiveModelInfo,
    val embed: ActiveModelInfo,
    val rerank: ActiveModelInfo,
)

// ---- User Preferences DTOs ----

data class UserPreferences(
    val thinkingMode: Boolean = false
)

data class UpdateUserPreferencesRequest(
    val thinkingMode: Boolean?     // null = no change
)
