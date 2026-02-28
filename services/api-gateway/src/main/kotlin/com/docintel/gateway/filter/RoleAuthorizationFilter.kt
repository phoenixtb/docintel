package com.docintel.gateway.filter

import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.context.annotation.Profile
import org.springframework.core.Ordered
import org.springframework.http.HttpMethod
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Component
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono

/**
 * Route-level RBAC enforcement filter (prod profile only).
 *
 * Runs after TenantFilter (order -99) which has already populated X-User-Role.
 * Denies with 403 if the authenticated role doesn't satisfy the route's requirement.
 *
 * Rules:
 *   /api/v1/admin/{*}      → platform_admin only
 *   /api/v1/tenants/{*}    → platform_admin only
 *   DELETE /api/v1/documents/all  → platform_admin or tenant_admin
 */
@Component
@Profile("prod")
class RoleAuthorizationFilter : GlobalFilter, Ordered {

    companion object {
        const val ROLE_PLATFORM_ADMIN = "platform_admin"
        const val ROLE_TENANT_ADMIN = "tenant_admin"
    }

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        val request = exchange.request
        val path = request.uri.path
        val method = request.method
        val role = request.headers.getFirst(TenantFilter.USER_ROLE_HEADER) ?: TenantFilter.DEFAULT_ROLE

        val denied = when {
            path.startsWith("/api/v1/admin/") || path == "/api/v1/admin" ->
                role != ROLE_PLATFORM_ADMIN

            path.startsWith("/api/v1/tenants/") || path == "/api/v1/tenants" ->
                role != ROLE_PLATFORM_ADMIN

            path == "/api/v1/documents/all" && method == HttpMethod.DELETE ->
                role != ROLE_PLATFORM_ADMIN && role != ROLE_TENANT_ADMIN

            else -> false
        }

        return if (denied) {
            exchange.response.statusCode = HttpStatus.FORBIDDEN
            exchange.response.setComplete()
        } else {
            chain.filter(exchange)
        }
    }

    override fun getOrder(): Int = -99
}
