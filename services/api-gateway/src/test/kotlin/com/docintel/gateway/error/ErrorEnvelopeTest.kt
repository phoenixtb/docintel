package com.docintel.gateway.error

import com.fasterxml.jackson.databind.ObjectMapper
import org.springframework.http.HttpStatus
import org.springframework.mock.http.server.reactive.MockServerHttpRequest
import org.springframework.mock.web.server.MockServerWebExchange
import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * Pins the {"error": {code, message, request_id}} wire format that every
 * gateway filter which short-circuits a request (401/403/429/503) must use.
 */
class ErrorEnvelopeTest {

    private val mapper = ObjectMapper()

    @Test
    fun `writes the standard error envelope with status, content-type, and request id`() {
        val request = MockServerHttpRequest.get("/api/v1/documents")
            .header("X-Request-Id", "req-123")
            .build()
        val exchange = MockServerWebExchange.from(request)

        writeErrorEnvelope(exchange, HttpStatus.FORBIDDEN, "FORBIDDEN", "Access denied by policy.").block()

        assertEquals(HttpStatus.FORBIDDEN, exchange.response.statusCode)
        assertEquals("application/json", exchange.response.headers.contentType?.toString())

        val bodyBytes = exchange.response.bodyAsString.block()!!
        val body = mapper.readTree(bodyBytes)
        assertEquals("FORBIDDEN", body["error"]["code"].asText())
        assertEquals("Access denied by policy.", body["error"]["message"].asText())
        assertEquals("req-123", body["error"]["request_id"].asText())
    }

    @Test
    fun `defaults request id to a dash when the header is absent`() {
        val request = MockServerHttpRequest.get("/api/v1/documents").build()
        val exchange = MockServerWebExchange.from(request)

        writeErrorEnvelope(exchange, HttpStatus.UNAUTHORIZED, "UNAUTHORIZED", "Missing token.").block()

        val body = mapper.readTree(exchange.response.bodyAsString.block()!!)
        assertEquals("-", body["error"]["request_id"].asText())
    }
}
