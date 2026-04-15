package com.docintel.admin.service

import com.docintel.admin.dto.PlatformSettings
import com.docintel.admin.dto.TenantSettings
import com.docintel.admin.dto.UpdatePlatformSettingsRequest
import com.docintel.admin.dto.UpdateTenantSettingsRequest
import org.slf4j.LoggerFactory
import org.springframework.dao.EmptyResultDataAccessException
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

@Service
class PlatformSettingsService(
    private val jdbcTemplate: JdbcTemplate,
) {
    private val log = LoggerFactory.getLogger(PlatformSettingsService::class.java)

    // ------------------------------------------------------------------
    // Platform-level settings
    // ------------------------------------------------------------------

    fun getPlatformSettings(): PlatformSettings {
        return try {
            val model = jdbcTemplate.queryForObject(
                "SELECT value FROM platform_settings WHERE key = 'llm_model'",
                String::class.java
            )
            // JSONB null arrives as the literal string "null"; treat that as no override.
            val parsed = if (model == null || model == "null") null
                         else model.trim('"')  // strip JSON quotes from a string value
            PlatformSettings(llmModel = parsed)
        } catch (e: EmptyResultDataAccessException) {
            PlatformSettings(llmModel = null)
        }
    }

    @Transactional
    fun updatePlatformSettings(req: UpdatePlatformSettingsRequest): PlatformSettings {
        // Store as JSONB: null → JSON null; a model name → JSON string "qwen3.5:4b"
        val jsonValue = if (req.llmModel == null) "null"
                        else "\"${req.llmModel}\""

        jdbcTemplate.update(
            """
            INSERT INTO platform_settings (key, value, updated_at)
            VALUES ('llm_model', ?::jsonb, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value,
                  updated_at = NOW()
            """.trimIndent(),
            jsonValue
        )
        log.info("Platform llm_model updated → {}", req.llmModel ?: "Tenant Choice")
        return getPlatformSettings()
    }

    // ------------------------------------------------------------------
    // Tenant-level settings
    // ------------------------------------------------------------------

    fun getTenantSettings(tenantId: String): TenantSettings {
        data class Row(val tenantModel: String?, val platformModel: String?, val thinkingMode: Boolean?)

        val row = jdbcTemplate.queryForObject(
            """
            SELECT
                t.settings->>'llm_model'                        AS tenant_model,
                (SELECT value FROM platform_settings WHERE key = 'llm_model') AS platform_model,
                (t.settings->>'thinking_mode')::boolean         AS thinking_mode
            FROM tenants t
            WHERE t.id = ?
            """.trimIndent(),
            { rs, _ ->
                Row(
                    tenantModel   = rs.getString("tenant_model"),
                    platformModel = rs.getString("platform_model"),
                    thinkingMode  = rs.getObject("thinking_mode") as? Boolean
                )
            },
            tenantId
        ) ?: return TenantSettings(llmModel = null, effectiveModel = null, thinkingMode = false)

        val platformOverride = if (row.platformModel == null || row.platformModel == "null") null
                               else row.platformModel.trim('"')
        val tenantPref = row.tenantModel
        val effective  = platformOverride ?: tenantPref

        return TenantSettings(
            llmModel      = tenantPref,
            effectiveModel = effective,
            thinkingMode  = row.thinkingMode ?: false,
        )
    }

    @Transactional
    fun updateTenantSettings(tenantId: String, req: UpdateTenantSettingsRequest): TenantSettings {
        if (req.llmModel == null) {
            jdbcTemplate.update(
                "UPDATE tenants SET settings = settings - 'llm_model', updated_at = NOW() WHERE id = ?",
                tenantId
            )
        } else {
            jdbcTemplate.update(
                "UPDATE tenants SET settings = jsonb_set(COALESCE(settings, '{}'), '{llm_model}', to_jsonb(?::text)), updated_at = NOW() WHERE id = ?",
                req.llmModel, tenantId
            )
        }

        if (req.thinkingMode != null) {
            jdbcTemplate.update(
                "UPDATE tenants SET settings = jsonb_set(COALESCE(settings, '{}'), '{thinking_mode}', to_jsonb(?::boolean)), updated_at = NOW() WHERE id = ?",
                req.thinkingMode, tenantId
            )
        }

        log.info("Tenant {} settings updated — llm_model={}, thinking_mode={}", tenantId, req.llmModel ?: "unchanged", req.thinkingMode ?: "unchanged")
        return getTenantSettings(tenantId)
    }
}
