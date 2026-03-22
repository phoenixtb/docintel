package com.docintel.admin.filter

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.LoggerFactory
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter

// Defense-in-depth: reject requests that bypassed the API Gateway.
// The gateway validates the JWT and sets X-User-Id on every authenticated request.
// If it is missing the caller reached this service directly, bypassing JWT validation
// and OPA authz. Reject with 403.
// Actuator paths are exempt so health checks continue to work.
@Component
@Order(1)
class GatewayAuthFilter : OncePerRequestFilter() {

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

        val userId = request.getHeader("X-User-Id")
        if (userId.isNullOrBlank()) {
            log.warn("Request to '{}' rejected: missing X-User-Id header (gateway bypass attempt)", path)
            response.status = HttpServletResponse.SC_FORBIDDEN
            response.contentType = "application/json"
            response.writer.write("{\"error\":\"Forbidden\",\"message\":\"Missing X-User-Id header. All requests must pass through the API Gateway.\"}")
            return
        }

        filterChain.doFilter(request, response)
    }
}
