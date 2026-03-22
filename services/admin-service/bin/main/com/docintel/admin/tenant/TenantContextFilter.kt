package com.docintel.admin.tenant

import jakarta.servlet.FilterChain
import jakarta.servlet.http.HttpServletRequest
import jakarta.servlet.http.HttpServletResponse
import org.springframework.core.annotation.Order
import org.springframework.stereotype.Component
import org.springframework.web.filter.OncePerRequestFilter

@Component
@Order(1)
class TenantContextFilter : OncePerRequestFilter() {

    override fun doFilterInternal(
        request: HttpServletRequest,
        response: HttpServletResponse,
        chain: FilterChain,
    ) {
        TenantContextHolder.set(
            TenantContext(
                tenantId = request.getHeader("X-Tenant-Id") ?: "default",
                userRole = request.getHeader("X-User-Role") ?: "tenant_user",
                userId   = request.getHeader("X-User-Id")  ?: "",
            )
        )
        try {
            chain.doFilter(request, response)
        } finally {
            TenantContextHolder.clear()
        }
    }
}
