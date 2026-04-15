package com.docintel.document.filter

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.MDC
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import java.util.UUID

/**
 * Extracts or generates a trace request ID and populates MDC so every log record
 * emitted during the request lifecycle carries the same trace fields.
 *
 * Must run BEFORE InternalAuthFilter (Order(0)) so auth log messages are also tagged.
 *
 * MDC keys:  requestId, tenantId, userId
 */
@Component("docintelRequestContextFilter")
@Order(0)
class RequestContextFilter : OncePerRequestFilter() {

    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        filterChain: FilterChain,
    ) {
        val requestId = request.getHeader("X-Request-Id")?.takeIf { it.isNotBlank() }
            ?: UUID.randomUUID().toString()
        val tenantId  = request.getHeader("X-Tenant-Id")  ?: "-"
        val userId    = request.getHeader("X-User-Id")    ?: "-"

        MDC.put("requestId", requestId)
        MDC.put("tenantId",  tenantId)
        MDC.put("userId",    userId)

        // Echo the (possibly generated) request ID back to the caller
        response.setHeader("X-Request-Id", requestId)

        try {
            filterChain.doFilter(request, response)
        } finally {
            MDC.remove("requestId")
            MDC.remove("tenantId")
            MDC.remove("userId")
        }
    }
}
