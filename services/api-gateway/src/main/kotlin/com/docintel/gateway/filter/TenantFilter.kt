package com.docintel.gateway.filter

import com.docintel.gateway.error.writeErrorEnvelope
import org.slf4j.LoggerFactory
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.http.HttpStatus
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
 * Backend services MUST NOT trust client-supplied X-* headers. Every identity header
 * above is removed from the incoming request before the gateway sets its own, so a
 * header the JWT has no claim for (e.g. X-User-Email on a machine token) is absent
 * downstream rather than client-controlled.
 *
 * A validated JWT without a non-blank "tenant_id" claim is rejected with 403
 * TENANT_CLAIM_MISSING. Falling back to a shared tenant would put every such caller
 * (e.g. when the Zitadel claims action fails) into the same tenant's data.
 *
 * Without a security context (only the "dev" profile, which permits everything), the
 * request is routed as tenant "default" with no user identity.
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
        const val TENANT_CLAIM_MISSING = "TENANT_CLAIM_MISSING"
        private val IDENTITY_HEADERS = listOf(
            TENANT_HEADER, USER_ID_HEADER, USER_EMAIL_HEADER,
            USER_NAME_HEADER, USER_ROLES_HEADER, USER_CLEARANCE_HEADER,
        )
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
                    ANONYMOUS
                }
            }
            .switchIfEmpty(Mono.fromCallable {
                log.warn(
                    "No JWT security context — defaulting to tenant=default (path={}). " +
                    "Check that this path is in permitAll().",
                    exchange.request.uri.path,
                )
                ANONYMOUS
            })
            .flatMap { identity ->
                when (identity) {
                    is JwtClaimData -> continueWithClaims(exchange, chain, identity)
                    MissingTenant -> {
                        log.warn(
                            "JWT has no tenant_id claim — rejecting (path={})",
                            exchange.request.uri.path,
                        )
                        writeErrorEnvelope(
                            exchange, HttpStatus.FORBIDDEN, TENANT_CLAIM_MISSING,
                            "Access token has no tenant.",
                        )
                    }
                }
            }
    }

    private fun extractFromJwt(jwt: Jwt): Identity {
        val tenantId = jwt.getClaimAsString(JWT_TENANT_CLAIM)?.takeIf { it.isNotBlank() }
            ?: return MissingTenant

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
            .headers { headers -> IDENTITY_HEADERS.forEach(headers::remove) }
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

    // Request with no security context: "dev" profile only (see class doc).
    private val ANONYMOUS: Identity =
        JwtClaimData(DEFAULT_TENANT, "", "", "", emptyList(), DEFAULT_CLEARANCE)

    private sealed interface Identity

    private data object MissingTenant : Identity

    private data class JwtClaimData(
        val tenantId: String,
        val userId: String,
        val userEmail: String,
        val userName: String,
        val userRoles: List<String>,
        val clearance: String,
    ) : Identity
}
