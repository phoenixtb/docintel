package com.docintel.gateway.error

import com.fasterxml.jackson.databind.ObjectMapper
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono

/**
 * Unified error response shape across all DocIntel services (Kotlin + Python):
 *
 *   {"error": {"code": "...", "message": "...", "request_id": "..."}}
 *
 * Gateway-emitted 401/403/429 previously returned an empty body or a
 * header-only signal (X-Quota-Exceeded) — every gateway filter that
 * short-circuits a request with an error status should call this instead of
 * `exchange.response.setComplete()` directly, so clients get a parseable body.
 */
private val mapper = ObjectMapper()

fun writeErrorEnvelope(
    exchange: ServerWebExchange,
    status: HttpStatus,
    code: String,
    message: String,
): Mono<Void> {
    val response = exchange.response
    response.statusCode = status
    response.headers.contentType = MediaType.APPLICATION_JSON
    val requestId = exchange.request.headers.getFirst("X-Request-Id") ?: "-"
    val body = mapOf(
        "error" to mapOf(
            "code" to code,
            "message" to message,
            "request_id" to requestId,
        )
    )
    val bytes = mapper.writeValueAsBytes(body)
    val buffer = response.bufferFactory().wrap(bytes)
    return response.writeWith(Mono.just(buffer))
}
