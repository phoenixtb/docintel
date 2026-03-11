package com.docintel.admin.controller

import com.docintel.admin.dto.*
import com.docintel.admin.service.CacheService
import com.docintel.admin.service.HealthService
import com.docintel.admin.service.PlatformSettingsService
import com.docintel.admin.service.StatsService
import com.docintel.admin.service.TenantManagementService
import jakarta.validation.Valid
import org.springframework.http.ResponseEntity
import org.springframework.validation.annotation.Validated
import org.springframework.web.bind.annotation.*

@Validated
@RestController
@RequestMapping("/internal")
class AdminController(
    private val healthService: HealthService,
    private val cacheService: CacheService,
    private val statsService: StatsService,
    private val tenantManagementService: TenantManagementService,
    private val platformSettingsService: PlatformSettingsService,
) {
    /**
     * System health check with component status.
     */
    @GetMapping("/health")
    fun health(): ResponseEntity<SystemHealth> {
        return ResponseEntity.ok(healthService.checkSystemHealth())
    }

    /**
     * System statistics.
     */
    @GetMapping("/stats")
    fun stats(): ResponseEntity<SystemStats> {
        return ResponseEntity.ok(statsService.getSystemStats())
    }

    /**
     * Cache statistics.
     */
    @GetMapping("/cache/stats")
    fun cacheStats(): ResponseEntity<CacheStats> {
        return ResponseEntity.ok(cacheService.getCacheStats())
    }

    /**
     * Clear all cache entries.
     */
    @PostMapping("/cache/clear")
    fun clearCache(): ResponseEntity<ClearCacheResponse> {
        return ResponseEntity.ok(cacheService.clearAllCache())
    }

    /**
     * Clear cache entries for a specific tenant.
     */
    @PostMapping("/cache/clear/{tenantId}")
    fun clearTenantCache(@PathVariable tenantId: String): ResponseEntity<ClearCacheResponse> {
        return ResponseEntity.ok(cacheService.clearTenantCache(tenantId))
    }

    /**
     * List all tenants.
     */
    @GetMapping("/tenants")
    fun listTenants(): ResponseEntity<List<TenantSummary>> {
        return ResponseEntity.ok(statsService.listTenants())
    }

    /**
     * Get usage statistics for a specific tenant.
     */
    @GetMapping("/tenants/{tenantId}/usage")
    fun getTenantUsage(@PathVariable tenantId: String): ResponseEntity<TenantUsage> {
        val usage = statsService.getTenantUsage(tenantId)
            ?: return ResponseEntity.notFound().build()
        return ResponseEntity.ok(usage)
    }

    /**
     * Create a new tenant (provisions PostgreSQL record + Authentik group).
     */
    @PostMapping("/tenants")
    fun createTenant(@Valid @RequestBody req: CreateTenantRequest): ResponseEntity<TenantSummary> {
        val tenant = tenantManagementService.createTenant(req)
        return ResponseEntity.ok(tenant)
    }

    /**
     * Update tenant name and/or quotas.
     */
    @PutMapping("/tenants/{tenantId}")
    fun updateTenant(
        @PathVariable tenantId: String,
        @Valid @RequestBody req: UpdateTenantRequest
    ): ResponseEntity<TenantSummary> {
        val tenant = tenantManagementService.updateTenant(tenantId, req)
            ?: return ResponseEntity.notFound().build()
        return ResponseEntity.ok(tenant)
    }

    /**
     * Delete a tenant and all associated data.
     */
    @DeleteMapping("/tenants/{tenantId}")
    fun deleteTenant(@PathVariable tenantId: String): ResponseEntity<DeleteTenantResponse> {
        val result = tenantManagementService.deleteTenant(tenantId)
        return if (result.success) ResponseEntity.ok(result)
        else ResponseEntity.notFound().build()
    }

    /**
     * List users for a tenant (sourced from Authentik).
     */
    @GetMapping("/tenants/{tenantId}/users")
    fun getTenantUsers(@PathVariable tenantId: String): ResponseEntity<List<TenantUser>> {
        return ResponseEntity.ok(tenantManagementService.getTenantUsers(tenantId))
    }

    /**
     * Update a user's role within a tenant.
     */
    @PutMapping("/tenants/{tenantId}/users/{userId}/role")
    fun updateUserRole(
        @PathVariable tenantId: String,
        @PathVariable userId: String,
        @RequestBody req: UpdateUserRoleRequest
    ): ResponseEntity<Void> {
        val updated = tenantManagementService.updateUserRole(tenantId, userId, req)
        return if (updated) ResponseEntity.ok().build()
        else ResponseEntity.badRequest().build()
    }

    // -----------------------------------------------------------------------
    // Platform-level settings (platform_admin only — enforced by OPA)
    // -----------------------------------------------------------------------

    /**
     * Get platform-wide settings (e.g. global LLM model override).
     */
    @GetMapping("/platform/settings")
    fun getPlatformSettings(): ResponseEntity<PlatformSettings> =
        ResponseEntity.ok(platformSettingsService.getPlatformSettings())

    /**
     * Update platform-wide settings.
     * Setting llmModel=null reverts to "Tenant Choice".
     * Cache invalidation is handled by the UI (separate cache/clear call).
     */
    @PutMapping("/platform/settings")
    fun updatePlatformSettings(
        @RequestBody req: UpdatePlatformSettingsRequest
    ): ResponseEntity<PlatformSettings> =
        ResponseEntity.ok(platformSettingsService.updatePlatformSettings(req))

    // -----------------------------------------------------------------------
    // Tenant-level model settings (tenant_admin for own tenant — enforced by OPA)
    // -----------------------------------------------------------------------

    /**
     * Get model settings for a specific tenant.
     */
    @GetMapping("/tenants/{tenantId}/settings")
    fun getTenantSettings(
        @PathVariable tenantId: String
    ): ResponseEntity<TenantSettings> {
        return ResponseEntity.ok(platformSettingsService.getTenantSettings(tenantId))
    }

    /**
     * Update model preference for a specific tenant.
     * Setting llmModel=null clears the preference (falls back to platform/default).
     * Cache invalidation is handled by the UI (separate cache/clear call).
     */
    @PatchMapping("/tenants/{tenantId}/settings")
    fun updateTenantSettings(
        @PathVariable tenantId: String,
        @RequestBody req: UpdateTenantSettingsRequest
    ): ResponseEntity<TenantSettings> =
        ResponseEntity.ok(platformSettingsService.updateTenantSettings(tenantId, req))
}
