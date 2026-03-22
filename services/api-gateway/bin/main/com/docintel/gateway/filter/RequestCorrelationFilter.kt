package com.docintel.gateway.filter

import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.cloud.gateway.filter.GlobalFilter
import org.springframework.core.Ordered
import org.springframework.stereotype.Component
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import java.util.UUID

/**
 * Generates a UUID-based X-Request-Id header for every inbound request if not already
 * present, and echoes it back in the response. Downstream services read this header
 * and include it in their log records for end-to-end request correlation.
 */
@Component
class RequestCorrelationFilter : GlobalFilter, Ordered {

    companion object {
        const val REQUEST_ID_HEADER = "X-Request-Id"
    }

    override fun getOrder(): Int = Ordered.HIGHEST_PRECEDENCE

    override fun filter(exchange: ServerWebExchange, chain: GatewayFilterChain): Mono<Void> {
        val requestId = exchange.request.headers.getFirst(REQUEST_ID_HEADER)
            ?: UUID.randomUUID().toString()

        val mutatedExchange = exchange.mutate()
            .request { req -> req.header(REQUEST_ID_HEADER, requestId) }
            .build()

        // Use beforeCommit so the header is set before the response is committed,
        // which also works correctly for SSE/streaming responses.
        mutatedExchange.response.beforeCommit {
            mutatedExchange.response.headers.putIfAbsent(REQUEST_ID_HEADER, listOf(requestId))
            Mono.empty()
        }

        return chain.filter(mutatedExchange)
    }
}
