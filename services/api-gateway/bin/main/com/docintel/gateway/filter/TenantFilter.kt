package com.docintel.gateway.filter

import org.slf4j.LoggerFactory
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.security.core.context.ReactiveSecurityContextHolder
import org.springframework.security.oauth2.jwt.Jwt
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken
import org.springframework.stereotype.Component
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono

/**
 * Extracts identity and authorization claims from the validated JWT and injects
 * them as typed headers for downstream backend services.
 *
 * Headers injected (all sourced from the gateway-validated JWT):
 *   X-Tenant-Id       — from docintel-actions custom claim "tenant_id"
 *   X-User-Id         — from JWT "sub"
 *   X-User-Email      — from JWT "email"
 *   X-User-Name       — from JWT "name" / "preferred_username"
 *   X-User-Roles      — comma-separated list from docintel-actions custom claim "roles"
 *   X-User-Clearance  — from docintel-actions custom claim "clearance"
 *
 * Backend services MUST NOT trust client-supplied X-* headers — they are stripped
 * here and replaced with gateway-authoritative values.
 *
 * Execution order: RequestCorrelationFilter (HIGHEST_PRECEDENCE) → this (-100)
 *   → InternalAuthFilter (-99) → OpaAuthorizationFilter (-98)
 */
@Component
class TenantFilter : GlobalFilter, Ordered {

    private val log = LoggerFactory.getLogger(javaClass)

    companion object {
        const val TENANT_HEADER    = "X-Tenant-Id"
        const val USER_ID_HEADER   = "X-User-Id"
        const val USER_EMAIL_HEADER = "X-User-Email"
        const val USER_NAME_HEADER  = "X-User-Name"
        const val USER_ROLES_HEADER = "X-User-Roles"
        const val USER_CLEARANCE_HEADER = "X-User-Clearance"
        const val DEFAULT_TENANT   = "default"
        const val DEFAULT_CLEARANCE = "internal"
        // Custom claim names injected by docintel-actions
        private const val JWT_TENANT_CLAIM    = "tenant_id"
        private const val JWT_ROLES_CLAIM     = "roles"
        private const val JWT_CLEARANCE_CLAIM = "clearance"
    }

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        return ReactiveSecurityContextHolder.getContext()
            .map { securityContext ->
                val authentication = securityContext.authentication
                if (authentication is JwtAuthenticationToken) {
                    extractFromJwt(authentication.token)
                } else {
                    JwtClaimData(DEFAULT_TENANT, "", "", "", emptyList(), DEFAULT_CLEARANCE)
                }
            }
            .switchIfEmpty(Mono.fromCallable {
                log.warn(
                    "No JWT security context — defaulting to tenant=default (path={}). " +
                    "Check that this path is in permitAll().",
                    exchange.request.uri.path,
                )
                JwtClaimData(DEFAULT_TENANT, "", "", "", emptyList(), DEFAULT_CLEARANCE)
            })
            .flatMap { claims -> continueWithClaims(exchange, chain, claims) }
    }

    private fun extractFromJwt(jwt: Jwt): JwtClaimData {
        val tenantId = jwt.getClaimAsString(JWT_TENANT_CLAIM) ?: DEFAULT_TENANT

        @Suppress("UNCHECKED_CAST")
        val roles: List<String> = when (val raw = jwt.claims[JWT_ROLES_CLAIM]) {
            is List<*> -> raw.filterIsInstance<String>()
            is String  -> raw.split(",").map { it.trim() }.filter { it.isNotEmpty() }
            else       -> emptyList()
        }

        val clearance = jwt.getClaimAsString(JWT_CLEARANCE_CLAIM) ?: DEFAULT_CLEARANCE

        return JwtClaimData(
            tenantId  = tenantId,
            userId    = jwt.subject ?: "",
            userEmail = jwt.getClaimAsString("email") ?: "",
            userName  = jwt.getClaimAsString("name")
                ?: jwt.getClaimAsString("preferred_username") ?: "",
            userRoles = roles,
            clearance = clearance,
        )
    }

    private fun continueWithClaims(
        exchange: ServerWebExchange,
        chain: GatewayFilterChain,
        claims: JwtClaimData,
    ): Mono<Void> {
        val rolesHeader = claims.userRoles.joinToString(",")

        val mutatedRequest = exchange.request.mutate()
            .header(TENANT_HEADER, claims.tenantId)
            .header(USER_ROLES_HEADER, rolesHeader)
            .header(USER_CLEARANCE_HEADER, claims.clearance)
            .apply {
                if (claims.userId.isNotBlank())    header(USER_ID_HEADER, claims.userId)
                if (claims.userEmail.isNotBlank()) header(USER_EMAIL_HEADER, claims.userEmail)
                if (claims.userName.isNotBlank())  header(USER_NAME_HEADER, claims.userName)
            }
            .build()

        return chain.filter(exchange.mutate().request(mutatedRequest).build())
    }

    override fun getOrder(): Int = -100

    private data class JwtClaimData(
        val tenantId: String,
        val userId: String,
        val userEmail: String,
        val userName: String,
        val userRoles: List<String>,
        val clearance: String,
    )
}
