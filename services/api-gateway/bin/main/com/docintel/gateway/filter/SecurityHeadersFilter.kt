package com.docintel.gateway.filter

import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.http.HttpHeaders
import org.springframework.stereotype.Component
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono

/**
 * Adds security response headers to every response:
 *   - Strict-Transport-Security (HSTS) — forces HTTPS for 1 year
 *   - X-Content-Type-Options          — prevents MIME-type sniffing
 *   - X-Frame-Options                 — prevents clickjacking
 *   - Content-Security-Policy         — restricts resource loading
 *   - Referrer-Policy                 — limits referrer leakage
 */
@Component
class SecurityHeadersFilter : GlobalFilter, Ordered {

    override fun getOrder(): Int = Ordered.HIGHEST_PRECEDENCE + 10

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        return chain.filter(exchange).then(Mono.fromRunnable {
            val headers: HttpHeaders = exchange.response.headers
            headers.putIfAbsent("Strict-Transport-Security", listOf("max-age=31536000; includeSubDomains"))
            headers.putIfAbsent("X-Content-Type-Options", listOf("nosniff"))
            headers.putIfAbsent("X-Frame-Options", listOf("DENY"))
            headers.putIfAbsent(
                "Content-Security-Policy",
                listOf("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'")
            )
            headers.putIfAbsent("Referrer-Policy", listOf("strict-origin-when-cross-origin"))
            headers.putIfAbsent("Permissions-Policy", listOf("geolocation=(), microphone=(), camera=()"))
        })
    }
}
