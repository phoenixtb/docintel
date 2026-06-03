package com.docintel.admin.repository

import com.docintel.admin.dto.ModelProfile
import com.docintel.admin.dto.UpsertModelProfileRequest
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.core.RowMapper
import org.springframework.stereotype.Repository
import java.util.UUID

@Repository
class ModelProfileRepository(
    private val jdbcTemplate: JdbcTemplate,
) {
    private val rowMapper = RowMapper<ModelProfile> { rs, _ ->
        ModelProfile(
            id                        = rs.getString("id"),
            scope                     = rs.getString("scope"),
            tenantId                  = rs.getString("tenant_id"),
            modelPattern              = rs.getString("model_pattern"),
            displayName               = rs.getString("display_name"),
            temperature               = rs.getObject("temperature")                as Double?,
            topP                      = rs.getObject("top_p")                     as Double?,
            maxTokens                 = rs.getObject("max_tokens")                as Int?,
            frequencyPenalty          = rs.getObject("frequency_penalty")         as Double?,
            presencePenalty           = rs.getObject("presence_penalty")          as Double?,
            repetitionPenalty         = rs.getObject("repetition_penalty")        as Double?,
            topK                      = rs.getObject("top_k")                     as Int?,
            minP                      = rs.getObject("min_p")                     as Double?,
            thinkingTemperature       = rs.getObject("thinking_temperature")      as Double?,
            thinkingTopP              = rs.getObject("thinking_top_p")            as Double?,
            thinkingMaxTokens         = rs.getObject("thinking_max_tokens")       as Int?,
            thinkingFrequencyPenalty  = rs.getObject("thinking_frequency_penalty") as Double?,
            thinkingPresencePenalty   = rs.getObject("thinking_presence_penalty") as Double?,
            thinkingRepetitionPenalty = rs.getObject("thinking_repetition_penalty") as Double?,
            thinkingTopK              = rs.getObject("thinking_top_k")            as Int?,
            thinkingMinP              = rs.getObject("thinking_min_p")            as Double?,
            thinkingBudget            = rs.getObject("thinking_budget")           as Int?,
            streamThinking            = rs.getObject("stream_thinking")           as Boolean?,
            kind                      = rs.getString("kind"),
            notes                     = rs.getString("notes"),
            createdAt                 = rs.getTimestamp("created_at").toInstant(),
            updatedAt                 = rs.getTimestamp("updated_at").toInstant(),
        )
    }

    fun listByScope(scope: String, tenantId: String? = null): List<ModelProfile> {
        return if (tenantId != null) {
            jdbcTemplate.query(
                "SELECT * FROM admin.model_profiles WHERE scope = ? AND tenant_id = ? ORDER BY model_pattern",
                rowMapper, scope, tenantId,
            )
        } else {
            jdbcTemplate.query(
                "SELECT * FROM admin.model_profiles WHERE scope = ? ORDER BY model_pattern",
                rowMapper, scope,
            )
        }
    }

    fun findById(id: String): ModelProfile? =
        jdbcTemplate.query(
            "SELECT * FROM admin.model_profiles WHERE id = ?::uuid",
            rowMapper, id,
        ).firstOrNull()

    fun create(scope: String, tenantId: String?, req: UpsertModelProfileRequest): ModelProfile {
        val id = UUID.randomUUID().toString()
        jdbcTemplate.update(
            """
            INSERT INTO admin.model_profiles (
                id, scope, tenant_id, model_pattern, display_name,
                temperature, top_p, max_tokens, frequency_penalty,
                presence_penalty, repetition_penalty, top_k, min_p,
                thinking_temperature, thinking_top_p, thinking_max_tokens,
                thinking_frequency_penalty, thinking_presence_penalty, thinking_repetition_penalty,
                thinking_top_k, thinking_min_p, thinking_budget, stream_thinking, kind, notes
            ) VALUES (?::uuid, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """.trimIndent(),
            id, scope, tenantId, req.modelPattern, req.displayName,
            req.temperature, req.topP, req.maxTokens, req.frequencyPenalty,
            req.presencePenalty, req.repetitionPenalty, req.topK, req.minP,
            req.thinkingTemperature, req.thinkingTopP, req.thinkingMaxTokens,
            req.thinkingFrequencyPenalty, req.thinkingPresencePenalty, req.thinkingRepetitionPenalty,
            req.thinkingTopK, req.thinkingMinP, req.thinkingBudget, req.streamThinking,
            normalizeKind(req.kind), req.notes,
        )
        return findById(id)!!
    }

    /**
     * Validate `kind` so a typo can't poison the column. NULL stays NULL (auto-infer).
     * Throws IllegalArgumentException for unrecognised values — surfaces as 400 to caller.
     */
    private fun normalizeKind(kind: String?): String? {
        if (kind == null) return null
        val k = kind.trim().lowercase()
        if (k.isEmpty()) return null
        require(k in ALLOWED_KINDS) {
            "Invalid kind '$kind'. Allowed: ${ALLOWED_KINDS.joinToString()}"
        }
        return k
    }

    companion object {
        private val ALLOWED_KINDS = setOf("chat", "vlm", "embed", "rerank")
    }

    fun update(id: String, req: UpsertModelProfileRequest): ModelProfile? {
        val rows = jdbcTemplate.update(
            """
            UPDATE admin.model_profiles SET
                model_pattern               = ?,
                display_name               = ?,
                temperature                = ?,
                top_p                      = ?,
                max_tokens                 = ?,
                frequency_penalty          = ?,
                presence_penalty           = ?,
                repetition_penalty         = ?,
                top_k                      = ?,
                min_p                      = ?,
                thinking_temperature       = ?,
                thinking_top_p             = ?,
                thinking_max_tokens        = ?,
                thinking_frequency_penalty = ?,
                thinking_presence_penalty  = ?,
                thinking_repetition_penalty= ?,
                thinking_top_k             = ?,
                thinking_min_p             = ?,
                thinking_budget            = ?,
                stream_thinking            = ?,
                kind                       = ?,
                notes                      = ?,
                updated_at                 = NOW()
            WHERE id = ?::uuid
            """.trimIndent(),
            req.modelPattern, req.displayName,
            req.temperature, req.topP, req.maxTokens, req.frequencyPenalty,
            req.presencePenalty, req.repetitionPenalty, req.topK, req.minP,
            req.thinkingTemperature, req.thinkingTopP, req.thinkingMaxTokens,
            req.thinkingFrequencyPenalty, req.thinkingPresencePenalty, req.thinkingRepetitionPenalty,
            req.thinkingTopK, req.thinkingMinP, req.thinkingBudget, req.streamThinking,
            normalizeKind(req.kind), req.notes,
            id,
        )
        return if (rows > 0) findById(id) else null
    }

    fun delete(id: String): Boolean =
        jdbcTemplate.update("DELETE FROM admin.model_profiles WHERE id = ?::uuid", id) > 0

    /** Updates only if the profile belongs to this tenant (scope=tenant, tenantId matches). */
    fun updateTenantProfile(tenantId: String, id: String, req: UpsertModelProfileRequest): ModelProfile? {
        val profile = findById(id) ?: return null
        if (profile.scope != "tenant" || profile.tenantId != tenantId) return null
        return update(id, req)
    }

    /** Deletes only if the profile belongs to this tenant. */
    fun deleteTenantProfile(tenantId: String, id: String): Boolean {
        val profile = findById(id) ?: return false
        if (profile.scope != "tenant" || profile.tenantId != tenantId) return false
        return delete(id)
    }

    /**
     * Copy platform-scoped non-wildcard profiles to the given tenant.
     * Skips patterns already present for the tenant.
     */
    fun seedFromPlatform(tenantId: String): List<ModelProfile> {
        val platform = listByScope("platform").filter { it.modelPattern != "*" }
        val existingPatterns = listByScope("tenant", tenantId).map { it.modelPattern }.toSet()
        return platform
            .filter { it.modelPattern !in existingPatterns }
            .map { p ->
                create(
                    "tenant", tenantId,
                    UpsertModelProfileRequest(
                        modelPattern              = p.modelPattern,
                        displayName               = p.displayName,
                        temperature               = p.temperature,
                        topP                      = p.topP,
                        maxTokens                 = p.maxTokens,
                        frequencyPenalty          = p.frequencyPenalty,
                        presencePenalty           = p.presencePenalty,
                        repetitionPenalty         = p.repetitionPenalty,
                        topK                      = p.topK,
                        minP                      = p.minP,
                        thinkingTemperature       = p.thinkingTemperature,
                        thinkingTopP              = p.thinkingTopP,
                        thinkingMaxTokens         = p.thinkingMaxTokens,
                        thinkingFrequencyPenalty  = p.thinkingFrequencyPenalty,
                        thinkingPresencePenalty   = p.thinkingPresencePenalty,
                        thinkingRepetitionPenalty = p.thinkingRepetitionPenalty,
                        thinkingTopK              = p.thinkingTopK,
                        thinkingMinP              = p.thinkingMinP,
                        thinkingBudget            = p.thinkingBudget,
                        streamThinking            = p.streamThinking,
                        kind                      = p.kind,
                        notes                     = "Seeded from platform profile.",
                    )
                )
            }
    }
}
