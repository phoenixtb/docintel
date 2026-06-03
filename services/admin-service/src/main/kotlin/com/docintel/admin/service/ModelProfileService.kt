package com.docintel.admin.service

import com.docintel.admin.dto.ModelProfile
import com.docintel.admin.dto.UpsertModelProfileRequest
import com.docintel.admin.repository.ModelProfileRepository
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service

@Service
class ModelProfileService(
    private val repo: ModelProfileRepository,
) {
    private val log = LoggerFactory.getLogger(ModelProfileService::class.java)

    fun listPlatformProfiles(): List<ModelProfile> =
        repo.listByScope("platform")

    fun listTenantProfiles(tenantId: String): List<ModelProfile> =
        repo.listByScope("tenant", tenantId)

    fun createPlatformProfile(req: UpsertModelProfileRequest): ModelProfile {
        val profile = repo.create("platform", null, req)
        log.info("Created platform model profile id={} pattern={}", profile.id, profile.modelPattern)
        return profile
    }

    fun createTenantProfile(tenantId: String, req: UpsertModelProfileRequest): ModelProfile {
        val profile = repo.create("tenant", tenantId, req)
        log.info("Created tenant model profile id={} tenant={} pattern={}", profile.id, tenantId, profile.modelPattern)
        return profile
    }

    fun updateProfile(id: String, req: UpsertModelProfileRequest): ModelProfile? {
        val profile = repo.update(id, req)
        if (profile != null) log.info("Updated model profile id={} pattern={}", id, req.modelPattern)
        return profile
    }

    fun deleteProfile(id: String): Boolean {
        val deleted = repo.delete(id)
        if (deleted) log.info("Deleted model profile id={}", id)
        return deleted
    }

    /** Update a tenant-owned profile — returns null if not found or doesn't belong to tenant. */
    fun updateTenantProfile(tenantId: String, id: String, req: UpsertModelProfileRequest): ModelProfile? {
        val profile = repo.updateTenantProfile(tenantId, id, req)
        if (profile != null) log.info("Updated tenant profile id={} tenant={} pattern={}", id, tenantId, req.modelPattern)
        return profile
    }

    /** Delete a tenant-owned profile — returns false if not found or doesn't belong to tenant. */
    fun deleteTenantProfile(tenantId: String, id: String): Boolean {
        val deleted = repo.deleteTenantProfile(tenantId, id)
        if (deleted) log.info("Deleted tenant profile id={} tenant={}", id, tenantId)
        return deleted
    }

    /**
     * Seed a tenant's model profiles.
     * Always ensures a '*' wildcard exists with concrete values.
     * Then copies any non-wildcard platform profiles not already present.
     * Safe to call on existing tenants — existing profiles are never overwritten.
     */
    fun seedTenantProfiles(tenantId: String): List<ModelProfile> {
        val existing = repo.listByScope("tenant", tenantId)
        val existingPatterns = existing.map { it.modelPattern }.toSet()

        // Guarantee a * wildcard with concrete defaults
        if ("*" !in existingPatterns) {
            val platformWildcard = repo.listByScope("platform").find { it.modelPattern == "*" }
            val req = if (platformWildcard != null) {
                UpsertModelProfileRequest(
                    modelPattern              = "*",
                    displayName               = "Tenant Defaults",
                    temperature               = platformWildcard.temperature,
                    topP                      = platformWildcard.topP,
                    maxTokens                 = platformWildcard.maxTokens,
                    frequencyPenalty          = platformWildcard.frequencyPenalty,
                    presencePenalty           = platformWildcard.presencePenalty,
                    repetitionPenalty         = platformWildcard.repetitionPenalty,
                    topK                      = platformWildcard.topK,
                    minP                      = platformWildcard.minP,
                    thinkingTemperature       = platformWildcard.thinkingTemperature,
                    thinkingTopP              = platformWildcard.thinkingTopP,
                    thinkingMaxTokens         = platformWildcard.thinkingMaxTokens,
                    thinkingFrequencyPenalty  = platformWildcard.thinkingFrequencyPenalty,
                    thinkingPresencePenalty   = platformWildcard.thinkingPresencePenalty,
                    thinkingRepetitionPenalty = platformWildcard.thinkingRepetitionPenalty,
                    thinkingTopK              = platformWildcard.thinkingTopK,
                    thinkingMinP              = platformWildcard.thinkingMinP,
                    thinkingBudget            = platformWildcard.thinkingBudget,
                    streamThinking            = platformWildcard.streamThinking,
                    kind                      = platformWildcard.kind,
                    notes                     = "Seeded from platform defaults.",
                )
            } else {
                // Full LMForge contract defaults — no platform profile available
                UpsertModelProfileRequest(
                    modelPattern              = "*",
                    displayName               = "Tenant Defaults",
                    temperature               = 0.1,
                    topP                      = null,
                    maxTokens                 = 1024,
                    frequencyPenalty          = 0.3,
                    thinkingTemperature       = 0.6,
                    thinkingTopP              = 0.95,
                    thinkingMaxTokens         = 6144,
                    thinkingFrequencyPenalty  = 0.0,
                    thinkingPresencePenalty   = 0.3,
                    thinkingRepetitionPenalty = 1.2,
                    thinkingTopK              = 20,
                    thinkingMinP              = 0.0,
                    thinkingBudget            = 4096,
                    streamThinking            = true,
                    notes                     = "Auto-seeded system defaults.",
                )
            }
            repo.create("tenant", tenantId, req)
            log.info("Created * wildcard profile for tenant={}", tenantId)
        }

        val seeded = repo.seedFromPlatform(tenantId)
        log.info("Seeded {} non-wildcard platform profile(s) for tenant={}", seeded.size, tenantId)
        return repo.listByScope("tenant", tenantId)
    }
}
