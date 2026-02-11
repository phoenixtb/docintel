package com.docintel.admin.controller

import com.docintel.admin.dto.*
import com.docintel.admin.service.CacheService
import com.docintel.admin.service.HealthService
import com.docintel.admin.service.StatsService
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*

@RestController
@RequestMapping("/internal")
class AdminController(
    private val healthService: HealthService,
    private val cacheService: CacheService,
    private val statsService: StatsService
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
}
