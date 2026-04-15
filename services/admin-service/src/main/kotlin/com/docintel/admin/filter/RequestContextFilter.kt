package com.docintel.admin.filter

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.slf4j.MDC
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter
import java.util.UUID

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
