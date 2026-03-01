package com.docintel.gateway.filter

import org.slf4j.LoggerFactory
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.data.redis.core.ReactiveStringRedisTemplate
import org.springframework.http.HttpMethod
import org.springframework.http.HttpStatus
import org.springframework.stereotype.Component
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import java.time.Duration
import java.time.LocalDate

/**
 * Quota enforcement filter using Redis counters.
 *
 * Enforces per-tenant quotas stored in PostgreSQL (quota_documents, quota_queries_per_day).
 * Redis counters are incremented on each qualifying request and compared against quota limits
 * stored in Redis (written by admin-service on tenant creation/update).
 *
 * Keys:
 *   quota:{tenant_id}:limit:documents        → max document count (set by admin-service)
 *   quota:{tenant_id}:doc_count              → current document count
 *   quota:{tenant_id}:limit:queries_per_day  → max daily queries (set by admin-service)
 *   quota:{tenant_id}:daily_queries:{date}   → today's query count (TTL = end of day)
 */
@Component
class QuotaEnforcementFilter(
    private val redis: ReactiveStringRedisTemplate,
) : GlobalFilter, Ordered {

    private val log = LoggerFactory.getLogger(javaClass)

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        val req = exchange.request
        val tenantId = req.headers.getFirst(TenantFilter.TENANT_HEADER) ?: return chain.filter(exchange)
        val path = req.uri.path
        val method = req.method

        return when {
            method == HttpMethod.POST && path.startsWith("/api/v1/documents") ->
                enforceDocumentQuota(tenantId, exchange, chain)

            method == HttpMethod.POST && (path == "/api/v1/query" || path == "/api/v1/query/stream") ->
                enforceQueryQuota(tenantId, exchange, chain)

            else -> chain.filter(exchange)
        }
    }

    private fun enforceDocumentQuota(
        tenantId: String,
        exchange: ServerWebExchange,
        chain: GatewayFilterChain,
    ): Mono<Void> {
        val limitKey = "quota:$tenantId:limit:documents"
        val countKey = "quota:$tenantId:doc_count"

        return redis.opsForValue().get(limitKey)
            .flatMap { limitStr ->
                val limit = limitStr?.toLongOrNull() ?: return@flatMap chain.filter(exchange)
                redis.opsForValue().get(countKey)
                    .flatMap { countStr ->
                        val count = countStr?.toLongOrNull() ?: 0L
                        if (count >= limit) {
                            log.warn("Document quota exceeded for tenant {}: {}/{}", tenantId, count, limit)
                            tooManyRequests(exchange, "Document quota exceeded")
                        } else {
                            chain.filter(exchange)
                        }
                    }
            }
            .switchIfEmpty(chain.filter(exchange))
    }

    private fun enforceQueryQuota(
        tenantId: String,
        exchange: ServerWebExchange,
        chain: GatewayFilterChain,
    ): Mono<Void> {
        val limitKey = "quota:$tenantId:limit:queries_per_day"
        val today = LocalDate.now().toString()
        val countKey = "quota:$tenantId:daily_queries:$today"

        return redis.opsForValue().get(limitKey)
            .flatMap { limitStr ->
                val limit = limitStr?.toLongOrNull() ?: return@flatMap chain.filter(exchange)
                redis.opsForValue().increment(countKey)
                    .flatMap { newCount ->
                        if (newCount == 1L) {
                            // First query today: set TTL to end of day (approx 24h)
                            redis.expire(countKey, Duration.ofHours(25)).subscribe()
                        }
                        if (newCount > limit) {
                            log.warn("Daily query quota exceeded for tenant {}: {}/{}", tenantId, newCount, limit)
                            // Decrement back since we over-counted
                            redis.opsForValue().decrement(countKey).subscribe()
                            tooManyRequests(exchange, "Daily query quota exceeded")
                        } else {
                            chain.filter(exchange)
                        }
                    }
            }
            .switchIfEmpty(chain.filter(exchange))
    }

    private fun tooManyRequests(exchange: ServerWebExchange, message: String): Mono<Void> {
        exchange.response.statusCode = HttpStatus.TOO_MANY_REQUESTS
        exchange.response.headers.add("X-Quota-Exceeded", message)
        return exchange.response.setComplete()
    }

    override fun getOrder(): Int = -98
}
