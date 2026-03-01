package com.docintel.gateway.filter

import org.slf4j.LoggerFactory
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Component
import org.springframework.web.reactive.function.client.WebClient
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono

/**
 * OPA-based authorization filter.
 *
 * Calls the shared OPA service for all RBAC decisions. Replaces the hardcoded
 * RoleAuthorizationFilter. Active in all profiles.
 *
 * Execution order: after TenantFilter (-100) → this (-99) → other filters.
 * Fail-closed: OPA down or network error → 403 Forbidden.
 */
@Component
class OpaAuthorizationFilter(
    private val opaWebClient: WebClient,
) : GlobalFilter, Ordered {

    private val log = LoggerFactory.getLogger(javaClass)

    private val publicPaths = setOf(
        "/actuator/health",
        "/actuator/info",
        "/api/v1/health",
    )

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        val req = exchange.request
        val path = req.uri.path

        // Skip OPA for public/health endpoints (already permitAll in SecurityConfig)
        if (publicPaths.any { path.startsWith(it) }) {
            return chain.filter(exchange)
        }

        val input = mapOf(
            "method"    to req.method.name(),
            "path"      to path,
            "role"      to (req.headers.getFirst(TenantFilter.USER_ROLE_HEADER) ?: TenantFilter.DEFAULT_ROLE),
            "tenant_id" to (req.headers.getFirst(TenantFilter.TENANT_HEADER) ?: TenantFilter.DEFAULT_TENANT),
        )

        return opaWebClient.post()
            .uri("/v1/data/docintel/authz/allow")
            .bodyValue(mapOf("input" to input))
            .retrieve()
            .bodyToMono(OpaResponse::class.java)
            .flatMap { resp ->
                if (resp.result == true) {
                    chain.filter(exchange)
                } else {
                    log.debug("OPA denied: path={} role={}", path, input["role"])
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
