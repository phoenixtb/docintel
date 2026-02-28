package com.docintel.admin.controller

import com.docintel.admin.dto.*
import com.docintel.admin.service.CacheService
import com.docintel.admin.service.HealthService
import com.docintel.admin.service.StatsService
import com.docintel.admin.service.TenantManagementService
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/internal")
class AdminController(
    private val healthService: HealthService,
    private val cacheService: CacheService,
    private val statsService: StatsService,
    private val tenantManagementService: TenantManagementService
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
    fun createTenant(@RequestBody req: CreateTenantRequest): ResponseEntity<TenantSummary> {
        val tenant = tenantManagementService.createTenant(req)
        return ResponseEntity.ok(tenant)
    }

    /**
     * Update tenant name and/or quotas.
     */
    @PutMapping("/tenants/{tenantId}")
    fun updateTenant(
        @PathVariable tenantId: String,
        @RequestBody req: UpdateTenantRequest
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
}
