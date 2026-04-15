package com.docintel.admin.service

import com.docintel.admin.dto.TenantQueryStats
import com.docintel.admin.filter.HmacUtils
import org.slf4j.LoggerFactory
import org.slf4j.MDC
import org.springframework.beans.factory.annotation.Value
import org.springframework.http.HttpEntity
import org.springframework.http.HttpHeaders
import org.springframework.http.HttpMethod
import org.springframework.http.client.SimpleClientHttpRequestFactory
import org.springframework.stereotype.Service
import org.springframework.web.client.RestTemplate

/**
 * Internal HTTP client for analytics-service (ClickHouse backend).
 *
 * Returns safe zeroes when analytics-service is unavailable so admin dashboards
 * do not fail due to an analytics outage.
 */
@Service
class AnalyticsServiceClient(
    @Value("\${analytics-service.url:http://analytics-service:8001}") private val analyticsUrl: String,
    @Value("\${internal.gateway.secret:}") private val internalSecret: String,
) {
    private val log = LoggerFactory.getLogger(AnalyticsServiceClient::class.java)
    private val rest = RestTemplate(SimpleClientHttpRequestFactory().apply {
        setConnectTimeout(3_000)
        setReadTimeout(10_000)
    })

    private fun headers(tenantId: String): HttpHeaders {
        if (internalSecret.isBlank()) {
            error("INTERNAL_GATEWAY_SECRET not configured — refusing unauthenticated internal call (fail-secure)")
        }
        val token = HmacUtils.compute(":$tenantId:", internalSecret)
        return HttpHeaders().apply {
            set("X-Tenant-Id", tenantId)
            set("X-Internal-Service-Token", token)
            MDC.get("requestId")?.let { set("X-Request-Id", it) }
        }
    }

    fun getTenantStats(tenantId: String): TenantQueryStats {
        return try {
            val response = rest.exchange(
                "$analyticsUrl/internal/analytics/tenant/$tenantId/stats",
                HttpMethod.GET,
                HttpEntity<Void>(headers(tenantId)),
                Map::class.java,
            )
            val body = response.body ?: emptyMap<Any, Any>()
            TenantQueryStats(
                totalQueries   = (body["queryCount"]      as? Number)?.toLong()   ?: 0L,
                queriesLast24h = (body["queryCountToday"] as? Number)?.toLong()   ?: 0L,
                cacheHitRate   = (body["cacheHitRate"]    as? Number)?.toDouble() ?: 0.0,
            )
        } catch (e: Exception) {
            log.debug("Analytics service unavailable for tenant {}: {} — returning zeroes", tenantId, e.message)
            TenantQueryStats()
        }
    }

    fun deleteTenantData(tenantId: String) {
        try {
            rest.exchange(
                "$analyticsUrl/internal/analytics/tenant/$tenantId",
                HttpMethod.DELETE,
                HttpEntity<Void>(headers(tenantId)),
                Void::class.java,
            )
            log.info("Deleted analytics data for tenant {}", tenantId)
        } catch (e: Exception) {
            log.warn("Failed to delete analytics data for tenant {} (non-fatal): {}", tenantId, e.message)
        }
    }
}
