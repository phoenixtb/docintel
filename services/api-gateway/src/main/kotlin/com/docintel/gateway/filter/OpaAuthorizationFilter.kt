package com.docintel.gateway.filter

import com.github.benmanes.caffeine.cache.Cache
import com.github.benmanes.caffeine.cache.Caffeine
import io.github.resilience4j.circuitbreaker.CircuitBreakerRegistry
import io.github.resilience4j.reactor.circuitbreaker.operator.CircuitBreakerOperator
import org.slf4j.LoggerFactory
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Component
import org.springframework.web.reactive.function.client.WebClient
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import java.time.Duration

/**
 * OPA-based authorization filter.
 *
 * Calls the shared OPA service for all RBAC decisions.
 * Wrapped in a Resilience4j circuit breaker — if OPA is repeatedly unreachable
 * the breaker opens and requests fail-closed (403) immediately without waiting.
 *
 * OPA decisions are deterministic for the same (method, normalizedPath, role, tenantId).
 * A Caffeine cache avoids the OPA HTTP round-trip for repeated identical inputs.
 * Cache key uses normalized path segments (UUIDs/IDs replaced with '*') so that
 * e.g. GET /api/v1/documents/abc-123 and GET /api/v1/documents/xyz-789 share one entry.
 *
 * Execution order: after TenantFilter (-100) → this (-99) → other filters.
 */
@Component
class OpaAuthorizationFilter(
    private val opaWebClient: WebClient,
    circuitBreakerRegistry: CircuitBreakerRegistry,
) : GlobalFilter, Ordered {

    private val log = LoggerFactory.getLogger(javaClass)
    private val opaCircuitBreaker = circuitBreakerRegistry.circuitBreaker("opa")

    private val publicPaths = setOf(
        "/actuator/health",
        "/actuator/info",
        "/api/v1/health",
    )

    // Cache OPA decisions for 60 seconds per (method, normalizedPath, role, tenantId).
    // Max 5,000 entries covers typical multi-tenant RBAC combinations.
    private val decisionCache: Cache<String, Boolean> = Caffeine.newBuilder()
        .maximumSize(5_000)
        .expireAfterWrite(Duration.ofSeconds(60))
        .recordStats()
        .build()

    // Regex that matches UUID-like segments and pure numeric IDs in URL paths
    private val idSegmentPattern = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|(?<=/)[0-9]+(?=/|\$)")

    private fun normalizePath(path: String): String =
        idSegmentPattern.replace(path, "*")

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        val req = exchange.request
        val path = req.uri.path

        if (publicPaths.any { path.startsWith(it) }) {
            return chain.filter(exchange)
        }

        val method = req.method.name()
        val role = req.headers.getFirst(TenantFilter.USER_ROLE_HEADER) ?: TenantFilter.DEFAULT_ROLE
        val tenantId = req.headers.getFirst(TenantFilter.TENANT_HEADER) ?: TenantFilter.DEFAULT_TENANT
        val normalizedPath = normalizePath(path)
        val cacheKey = "$method:$normalizedPath:$role:$tenantId"

        val cached = decisionCache.getIfPresent(cacheKey)
        if (cached != null) {
            log.trace("OPA cache hit: key={}", cacheKey)
            return if (cached) chain.filter(exchange) else forbidden(exchange)
        }

        val input = mapOf(
            "method"    to method,
            "path"      to path,
            "role"      to role,
            "tenant_id" to tenantId,
        )

        return opaWebClient.post()
            .uri("/v1/data/docintel/authz/allow")
            .bodyValue(mapOf("input" to input))
            .retrieve()
            .bodyToMono(OpaResponse::class.java)
            .transformDeferred(CircuitBreakerOperator.of(opaCircuitBreaker))
            .flatMap { resp ->
                val allowed = resp.result == true
                decisionCache.put(cacheKey, allowed)
                log.trace("OPA decision: allowed={} key={}", allowed, cacheKey)
                if (allowed) {
                    chain.filter(exchange)
                } else {
                    log.debug("OPA denied: path={} role={}", path, role)
                    forbidden(exchange)
                }
            }
            .onErrorResume { ex ->
                log.warn("OPA call failed (fail-closed): {}", ex.message)
                forbidden(exchange)
            }
    }

    private fun forbidden(exchange: ServerWebExchange): Mono<Void> {
        exchange.response.statusCode = HttpStatus.FORBIDDEN
        return exchange.response.setComplete()
    }

    override fun getOrder(): Int = -99

    data class OpaResponse(val result: Boolean? = null)
}
