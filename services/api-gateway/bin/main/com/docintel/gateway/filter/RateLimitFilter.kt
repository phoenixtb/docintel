package com.docintel.gateway.filter

import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Component
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicInteger
import java.time.Instant

/**
 * Simple in-memory rate limiter.
 * For production, use Redis-based rate limiting.
 */
@Component
class RateLimitFilter : GlobalFilter, Ordered {

    companion object {
        const val DEFAULT_LIMIT = 100 // requests per minute
        const val QUERY_LIMIT = 20   // expensive RAG queries per minute
    }

    // In-memory rate limit tracking (use Redis for production)
    private val rateLimits = ConcurrentHashMap<String, RateLimitEntry>()

    data class RateLimitEntry(
        val count: AtomicInteger = AtomicInteger(0),
        @Volatile var windowStart: Long = System.currentTimeMillis()
    )

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        val request = exchange.request
        val path = request.path.value()
        val tenantId = request.headers.getFirst(TenantFilter.TENANT_HEADER) ?: TenantFilter.DEFAULT_TENANT

        // Determine rate limit based on path
        val limit = when {
            path.contains("/query") -> QUERY_LIMIT
            else -> DEFAULT_LIMIT
        }

        val key = "$tenantId:${if (path.contains("/query")) "query" else "default"}"

        // Check rate limit
        if (!checkRateLimit(key, limit)) {
            exchange.response.statusCode = HttpStatus.TOO_MANY_REQUESTS
            exchange.response.headers.add("X-RateLimit-Remaining", "0")
            exchange.response.headers.add("Retry-After", "60")
            return exchange.response.setComplete()
        }

        // Add rate limit headers
        val entry = rateLimits[key]
        val remaining = limit - (entry?.count?.get() ?: 0)
        exchange.response.headers.add("X-RateLimit-Limit", limit.toString())
        exchange.response.headers.add("X-RateLimit-Remaining", remaining.coerceAtLeast(0).toString())

        return chain.filter(exchange)
    }

    private fun checkRateLimit(key: String, limit: Int): Boolean {
        val entry = rateLimits.computeIfAbsent(key) { RateLimitEntry() }
        val now = System.currentTimeMillis()

        // Reset window if expired (1 minute window)
        if (now - entry.windowStart > 60_000) {
            entry.count.set(0)
            entry.windowStart = now
        }

        return entry.count.incrementAndGet() <= limit
    }

    override fun getOrder(): Int = -50 // Run after tenant filter
}
