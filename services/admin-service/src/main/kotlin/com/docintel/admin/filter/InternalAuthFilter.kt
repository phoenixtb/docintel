package com.docintel.admin.filter

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter

/**
 * Unified authentication filter for admin-service.
 *
 * The API gateway rewrites /api/v1/admin/X to /internal/X, so this service
 * receives both gateway-proxied user requests (X-User-Id) and direct
 * service-to-service calls (X-Internal-Service-Token + HMAC).
 *
 * Rules:
 *   /actuator/   — open
 *   everything else:
 *     1. X-Internal-Service-Token present → validate HMAC (fail-secure if no secret)
 *     2. X-User-Id present → gateway-proxied user request, allow through
 *     3. Neither → 403
 */
@Component
@Order(1)
class InternalAuthFilter(
    @Value("\${internal.gateway.secret:}") private val secret: String,
) : OncePerRequestFilter() {

    private val log = LoggerFactory.getLogger(javaClass)

    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        val path = request.requestURI

        if (path.startsWith("/actuator")) {
            filterChain.doFilter(request, response)
            return
        }

        val token  = request.getHeader("X-Internal-Service-Token")
        val userId = request.getHeader("X-User-Id")

        when {
            !token.isNullOrBlank() -> {
                if (secret.isBlank()) {
                    log.error("INTERNAL_GATEWAY_SECRET not configured — rejecting service call to '{}' (fail-secure)", path)
                    forbidden(response, "Internal auth secret not configured")
                    return
                }
                val requestId = request.getHeader("X-Request-Id") ?: ""
                val tenantId  = request.getHeader("X-Tenant-Id")  ?: ""
                val gwUserId  = userId ?: ""

                val gatewayValid = HmacUtils.verify(token, requestId, tenantId, gwUserId, secret)
                val serviceValid = HmacUtils.verify(token, "",         tenantId, "",       secret)

                if (!gatewayValid && !serviceValid) {
                    log.warn("Invalid X-Internal-Service-Token for '{}' (tenant={})", path, tenantId)
                    forbidden(response, "Invalid X-Internal-Service-Token")
                    return
                }
                filterChain.doFilter(request, response)
            }

            !userId.isNullOrBlank() -> {
                filterChain.doFilter(request, response)
            }

            else -> {
                log.warn("Request to '{}' rejected: no X-User-Id or X-Internal-Service-Token", path)
                forbidden(response, "Missing authentication headers. Use API Gateway or provide X-Internal-Service-Token.")
                return
            }
        }
    }

    private fun forbidden(response: HttpServletResponse, message: String) {
        response.status = HttpServletResponse.SC_FORBIDDEN
        response.contentType = "application/json"
        response.writer.write("""{"error":"Forbidden","message":"$message"}""")
    }
}
