package com.docintel.admin.service

import com.docintel.admin.dto.PlatformSettings
import com.docintel.admin.dto.TenantSettings
import com.docintel.admin.dto.UpdatePlatformSettingsRequest
import com.docintel.admin.dto.UpdateTenantSettingsRequest
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.dao.EmptyResultDataAccessException
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

/**
 * Platform & tenant model preference store.
 *
 * Three model kinds are runtime-tunable (chat, vlm, rerank); embed is env-only.
 *
 * Storage:
 *  - platform-wide overrides: `admin.platform_settings` rows keyed by
 *    `llm_model` (chat — legacy name), `llm_vlm_model`, `llm_rerank_model`.
 *  - per-tenant prefs: JSONB keys with the same names inside `admin.tenants.settings`.
 *
 * Effective value (used by the UI's "Active Models" panel) =
 *    platform override (if set) -> tenant pref (if set) -> env var fallback.
 */
@Service
class PlatformSettingsService(
    private val jdbcTemplate: JdbcTemplate,
    @Value("\${LLM_MODEL:}")        private val envChatModel: String,
    @Value("\${LLM_VLM_MODEL:}")    private val envVlmModel: String,
    @Value("\${LLM_RERANK_MODEL:}") private val envRerankModel: String,
) {
    private val log = LoggerFactory.getLogger(PlatformSettingsService::class.java)

    // ---- Platform-wide model overrides -------------------------------------

    fun getPlatformSettings(): PlatformSettings = PlatformSettings(
        llmModel       = readPlatformKey("llm_model"),
        llmVlmModel    = readPlatformKey("llm_vlm_model"),
        llmRerankModel = readPlatformKey("llm_rerank_model"),
    )

    @Transactional
    fun updatePlatformSettings(req: UpdatePlatformSettingsRequest): PlatformSettings {
        // null -> clear override; non-null -> store override.
        // This is a **full replace** semantically: all three fields in the
        // request are written. The UI sends the full payload to keep things
        // simple — partial updates would require Optional<>-style wrappers.
        upsertPlatformKey("llm_model",        req.llmModel)
        upsertPlatformKey("llm_vlm_model",    req.llmVlmModel)
        upsertPlatformKey("llm_rerank_model", req.llmRerankModel)
        log.info(
            "Platform model overrides updated: chat={} vlm={} rerank={}",
            req.llmModel ?: "(tenant choice)",
            req.llmVlmModel ?: "(tenant choice)",
            req.llmRerankModel ?: "(tenant choice)",
        )
        return getPlatformSettings()
    }

    // ---- Per-tenant prefs --------------------------------------------------

    fun getTenantSettings(tenantId: String): TenantSettings {
        data class Row(
            val tenantChat:   String?, val tenantVlm:    String?, val tenantRerank:    String?,
            val platformChat: String?, val platformVlm:  String?, val platformRerank:  String?,
        )

        val row = jdbcTemplate.queryForObject(
            """
            SELECT
                t.settings->>'llm_model'        AS tenant_chat,
                t.settings->>'llm_vlm_model'    AS tenant_vlm,
                t.settings->>'llm_rerank_model' AS tenant_rerank,
                (SELECT value FROM admin.platform_settings WHERE key='llm_model')        AS platform_chat,
                (SELECT value FROM admin.platform_settings WHERE key='llm_vlm_model')    AS platform_vlm,
                (SELECT value FROM admin.platform_settings WHERE key='llm_rerank_model') AS platform_rerank
            FROM admin.tenants t
            WHERE t.id = ?
            """.trimIndent(),
            { rs, _ ->
                Row(
                    tenantChat    = rs.getString("tenant_chat"),
                    tenantVlm     = rs.getString("tenant_vlm"),
                    tenantRerank  = rs.getString("tenant_rerank"),
                    platformChat   = rs.getString("platform_chat"),
                    platformVlm    = rs.getString("platform_vlm"),
                    platformRerank = rs.getString("platform_rerank"),
                )
            },
            tenantId
        ) ?: return TenantSettings(
            llmModel = null, llmVlmModel = null, llmRerankModel = null,
            effectiveModel = envChatModel.ifBlank { null },
            effectiveVlmModel = envVlmModel.ifBlank { null },
            effectiveRerankModel = envRerankModel.ifBlank { null },
        )

        val platformChat   = parseJsonbStringOrNull(row.platformChat)
        val platformVlm    = parseJsonbStringOrNull(row.platformVlm)
        val platformRerank = parseJsonbStringOrNull(row.platformRerank)

        return TenantSettings(
            llmModel       = row.tenantChat,
            llmVlmModel    = row.tenantVlm,
            llmRerankModel = row.tenantRerank,
            effectiveModel       = platformChat   ?: row.tenantChat   ?: envChatModel.ifBlank { null },
            effectiveVlmModel    = platformVlm    ?: row.tenantVlm    ?: envVlmModel.ifBlank { null },
            effectiveRerankModel = platformRerank ?: row.tenantRerank ?: envRerankModel.ifBlank { null },
        )
    }

    @Transactional
    fun updateTenantSettings(tenantId: String, req: UpdateTenantSettingsRequest): TenantSettings {
        upsertTenantKey(tenantId, "llm_model",        req.llmModel)
        upsertTenantKey(tenantId, "llm_vlm_model",    req.llmVlmModel)
        upsertTenantKey(tenantId, "llm_rerank_model", req.llmRerankModel)
        log.info(
            "Tenant {} model prefs updated: chat={} vlm={} rerank={}",
            tenantId,
            req.llmModel ?: "(unset)",
            req.llmVlmModel ?: "(unset)",
            req.llmRerankModel ?: "(unset)",
        )
        return getTenantSettings(tenantId)
    }

    // ---- Helpers -----------------------------------------------------------

    /** Effective resolved model id per kind, or null if neither DB nor env has one. */
    fun effectiveFor(kind: String, tenantId: String): EffectiveModel {
        val ts = getTenantSettings(tenantId)
        val (effective, envFb) = when (kind) {
            "chat"   -> ts.effectiveModel       to envChatModel.ifBlank { null }
            "vlm"    -> ts.effectiveVlmModel    to envVlmModel.ifBlank { null }
            "rerank" -> ts.effectiveRerankModel to envRerankModel.ifBlank { null }
            else     -> null to null
        }
        // Source: platform override beats tenant beats env.
        val ps = getPlatformSettings()
        val platformVal = when (kind) {
            "chat"   -> ps.llmModel
            "vlm"    -> ps.llmVlmModel
            "rerank" -> ps.llmRerankModel
            else     -> null
        }
        val tenantVal = when (kind) {
            "chat"   -> ts.llmModel
            "vlm"    -> ts.llmVlmModel
            "rerank" -> ts.llmRerankModel
            else     -> null
        }
        val source = when {
            platformVal != null              -> "platform"
            tenantVal   != null              -> "tenant"
            envFb       != null              -> "env"
            else                              -> "none"
        }
        return EffectiveModel(model = effective, source = source, envFallback = envFb)
    }

    /** What the embed kind effectively resolves to (env-only, never DB). */
    fun envEmbed(): String? = System.getenv("LLM_EMBED_MODEL")?.ifBlank { null }
        ?: System.getProperty("LLM_EMBED_MODEL")?.ifBlank { null }

    data class EffectiveModel(val model: String?, val source: String, val envFallback: String?)

    // ---- Internals ---------------------------------------------------------

    private fun readPlatformKey(key: String): String? {
        return try {
            val raw = jdbcTemplate.queryForObject(
                "SELECT value FROM admin.platform_settings WHERE key = ?",
                String::class.java,
                key,
            )
            parseJsonbStringOrNull(raw)
        } catch (_: EmptyResultDataAccessException) { null }
    }

    private fun upsertPlatformKey(key: String, value: String?) {
        val jsonValue = if (value == null) "null" else "\"$value\""
        jdbcTemplate.update(
            """
            INSERT INTO admin.platform_settings (key, value, updated_at)
            VALUES (?, ?::jsonb, NOW())
            ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = NOW()
            """.trimIndent(),
            key, jsonValue,
        )
    }

    private fun upsertTenantKey(tenantId: String, key: String, value: String?) {
        if (value == null) {
            jdbcTemplate.update(
                "UPDATE admin.tenants SET settings = settings - ?, updated_at = NOW() WHERE id = ?",
                key, tenantId,
            )
        } else {
            jdbcTemplate.update(
                "UPDATE admin.tenants SET settings = jsonb_set(COALESCE(settings, '{}'), ARRAY[?], to_jsonb(?::text)), updated_at = NOW() WHERE id = ?",
                key, value, tenantId,
            )
        }
    }

    /** JSONB string values arrive as the literal "null" or a quoted string. */
    private fun parseJsonbStringOrNull(raw: String?): String? {
        if (raw == null || raw == "null") return null
        return raw.trim('"')
    }
}
