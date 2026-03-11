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
 * Global filter that:
 * 1. Passes the full Authorization header to downstream services (for claim extraction)
 * 2. Extracts tenant_id from JWT and adds as X-Tenant-Id header
 * 3. Extracts user info and adds as X-User-* headers
 * 
 * Backend services receive the full JWT and can extract any claims they need
 * for logging, auditing, authorization, etc.
 */
@Component
class TenantFilter : GlobalFilter, Ordered {

    private val log = LoggerFactory.getLogger(javaClass)

    companion object {
        const val TENANT_HEADER = "X-Tenant-Id"
        const val USER_ID_HEADER = "X-User-Id"
        const val USER_EMAIL_HEADER = "X-User-Email"
        const val USER_NAME_HEADER = "X-User-Name"
        const val USER_ROLE_HEADER = "X-User-Role"
        const val DEFAULT_TENANT = "default"
        const val DEFAULT_ROLE = "tenant_user"
        const val JWT_TENANT_CLAIM = "tenant_id"
        const val JWT_ROLE_CLAIM = "role"
    }

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        // Always extract tenant from JWT — never trust client-provided X-Tenant-Id headers
        return ReactiveSecurityContextHolder.getContext()
            .map { securityContext ->
                val authentication = securityContext.authentication
                if (authentication is JwtAuthenticationToken) {
                    val jwt: Jwt = authentication.token
                    JwtClaimData(
                        tenantId = extractTenantId(jwt),
                        userId = jwt.subject ?: "",
                        userEmail = jwt.getClaimAsString("email") ?: "",
                        userName = jwt.getClaimAsString("name")
                            ?: jwt.getClaimAsString("preferred_username") ?: "",
                        userRole = jwt.getClaimAsString(JWT_ROLE_CLAIM) ?: DEFAULT_ROLE
                    )
                } else {
                    JwtClaimData(DEFAULT_TENANT, "", "", "", DEFAULT_ROLE)
                }
            }
            .switchIfEmpty(Mono.fromCallable {
                log.warn("No JWT security context found — defaulting to tenant=default (path={}). Check that this path is in permitAll().", exchange.request.uri.path)
                JwtClaimData(DEFAULT_TENANT, "", "", "", DEFAULT_ROLE)
            })
            .flatMap { claims ->
                continueWithClaims(exchange, chain, claims)
            }
    }
    
    private fun extractTenantId(jwt: Jwt): String {
        // Try direct claim first
        var tenantId = jwt.getClaimAsString(JWT_TENANT_CLAIM)
        
        // If not found, try nested in custom claims (Authentik scope format)
        if (tenantId.isNullOrBlank()) {
            @Suppress("UNCHECKED_CAST")
            val tenantClaim = jwt.claims["tenant"] as? Map<String, Any>
            tenantId = tenantClaim?.get("tenant_id") as? String
        }
        
        // Try groups attribute (Authentik group-based tenant)
        if (tenantId.isNullOrBlank()) {
            @Suppress("UNCHECKED_CAST")
            val groups = jwt.claims["groups"] as? List<String>
            // Convention: tenant group is named "tenant-<tenant_id>"
            tenantId = groups?.firstOrNull { it.startsWith("tenant-") }
                ?.removePrefix("tenant-")
        }
        
        return tenantId ?: DEFAULT_TENANT
    }
    
    private fun continueWithClaims(
        exchange: ServerWebExchange,
        chain: GatewayFilterChain,
        claims: JwtClaimData
    ): Mono<Void> {
        // Build mutated request with extracted claims as headers
        // The original Authorization header is preserved automatically
        val mutatedRequest = exchange.request.mutate()
            .header(TENANT_HEADER, claims.tenantId)
            .header(USER_ROLE_HEADER, claims.userRole)
            .apply {
                if (claims.userId.isNotBlank()) {
                    header(USER_ID_HEADER, claims.userId)
                }
                if (claims.userEmail.isNotBlank()) {
                    header(USER_EMAIL_HEADER, claims.userEmail)
                }
                if (claims.userName.isNotBlank()) {
                    header(USER_NAME_HEADER, claims.userName)
                }
            }
            .build()

        return chain.filter(exchange.mutate().request(mutatedRequest).build())
    }

    override fun getOrder(): Int = -100 // Run early, after security filter
    
    private data class JwtClaimData(
        val tenantId: String,
        val userId: String,
        val userEmail: String,
        val userName: String,
        val userRole: String
    )
}
