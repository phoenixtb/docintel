package com.docintel.gateway.filter

import org.slf4j.LoggerFactory
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Component
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Adds X-Internal-Service-Token to every forwarded request.
 *
 * Computes HMAC-SHA256("{requestId}:{tenantId}:{userId}", INTERNAL_GATEWAY_SECRET)
 * so backend services can verify the request originated from the gateway and not
 * a direct caller that bypassed JWT validation and OPA authorization.
 *
 * Execution order:
 *   TenantFilter (-100) → InternalAuthFilter (-99) → OpaAuthorizationFilter (-98) → ...
 *
 * The secret is read once at construction time from the INTERNAL_GATEWAY_SECRET env var.
 * If the secret is absent the gateway returns 503 SERVICE_UNAVAILABLE on every request
 * (fail-secure) — run setup.sh to generate and populate the secret.
 */
@Component
class InternalAuthFilter : GlobalFilter, Ordered {

    private val log = LoggerFactory.getLogger(javaClass)

    companion object {
        const val INTERNAL_TOKEN_HEADER = "X-Internal-Service-Token"
        private const val HMAC_ALGORITHM = "HmacSHA256"
    }

    private val secret: String = System.getenv("INTERNAL_GATEWAY_SECRET")
        ?.takeIf { it.isNotEmpty() }
        ?: run {
            log.error("INTERNAL_GATEWAY_SECRET is not set — gateway will reject all requests (fail-secure). Run setup.sh.")
            ""
        }

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        if (secret.isEmpty()) {
            log.error("INTERNAL_GATEWAY_SECRET not configured — rejecting request (fail-secure)")
            exchange.response.statusCode = HttpStatus.SERVICE_UNAVAILABLE
            return exchange.response.setComplete()
        }

        val req = exchange.request
        val requestId = req.headers.getFirst(RequestCorrelationFilter.REQUEST_ID_HEADER) ?: ""
        val tenantId  = req.headers.getFirst(TenantFilter.TENANT_HEADER) ?: ""
        val userId    = req.headers.getFirst(TenantFilter.USER_ID_HEADER) ?: ""

        val token = computeToken(requestId, tenantId, userId)

        val mutatedRequest = req.mutate()
            .header(INTERNAL_TOKEN_HEADER, token)
            .build()

        return chain.filter(exchange.mutate().request(mutatedRequest).build())
    }

    private fun computeToken(requestId: String, tenantId: String, userId: String): String {
        val message = "$requestId:$tenantId:$userId".toByteArray(Charsets.UTF_8)
        val mac = Mac.getInstance(HMAC_ALGORITHM)
        mac.init(SecretKeySpec(secret.toByteArray(Charsets.UTF_8), HMAC_ALGORITHM))
        return mac.doFinal(message).joinToString("") { "%02x".format(it) }
    }

    override fun getOrder(): Int = -99
}
