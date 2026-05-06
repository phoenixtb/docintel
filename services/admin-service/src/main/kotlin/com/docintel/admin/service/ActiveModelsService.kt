package com.docintel.admin.service

import com.docintel.admin.dto.ActiveModelInfo
import com.docintel.admin.dto.ActiveModels
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service

/**
 * Resolves the **effective** chat / vlm / embed / rerank models for a given
 * tenant, taking into account:
 *   1. Platform-wide override (admin.platform_settings)
 *   2. Tenant preference     (admin.tenants.settings JSONB)
 *   3. Env fallback          (LLM_*_MODEL)
 *
 * Embed is intentionally env-only (`tunable=false`): changing it would
 * invalidate every vector in the store.
 */
@Service
class ActiveModelsService(
    private val platformSettingsService: PlatformSettingsService,
    @Value("\${LLM_EMBED_MODEL:}") private val embedModel: String,
    @Value("\${USE_RERANKING:false}") private val useReranking: Boolean,
) {

    fun get(tenantId: String): ActiveModels {
        val chat   = platformSettingsService.effectiveFor("chat",   tenantId)
        val vlm    = platformSettingsService.effectiveFor("vlm",    tenantId)
        val rerank = platformSettingsService.effectiveFor("rerank", tenantId)

        return ActiveModels(
            chat = ActiveModelInfo(
                model       = chat.model,
                kind        = "chat",
                source      = chat.source,
                envFallback = chat.envFallback,
                tunable     = true,
                disabled    = chat.model.isNullOrBlank(),
            ),
            vlm = ActiveModelInfo(
                model       = vlm.model,
                kind        = "vlm",
                source      = vlm.source,
                envFallback = vlm.envFallback,
                tunable     = true,
                disabled    = vlm.model.isNullOrBlank(),
            ),
            embed = ActiveModelInfo(
                model       = embedModel.ifBlank { null },
                kind        = "embed",
                source      = if (embedModel.isNotBlank()) "env" else "none",
                envFallback = embedModel.ifBlank { null },
                tunable     = false,           // changing embed invalidates the vector store
                disabled    = embedModel.isBlank(),
            ),
            rerank = ActiveModelInfo(
                model       = rerank.model,
                kind        = "rerank",
                source      = rerank.source,
                envFallback = rerank.envFallback,
                tunable     = true,
                disabled    = !useReranking || rerank.model.isNullOrBlank(),
            ),
        )
    }
}
