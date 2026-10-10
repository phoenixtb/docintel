package com.docintel.gateway.filter

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.Test
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.http.HttpStatus
import org.springframework.mock.http.server.reactive.MockServerHttpRequest
import org.springframework.mock.web.server.MockServerWebExchange
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import kotlin.test.assertContains
import kotlin.test.assertEquals

class InternalAuthFilterTest {

    private val chain: GatewayFilterChain = mockk()

    private fun request() = MockServerHttpRequest.get("/api/v1/documents")
        .header(RequestCorrelationFilter.REQUEST_ID_HEADER, "req-1")
        .header(TenantFilter.TENANT_HEADER, "acme")
        .header(TenantFilter.USER_ID_HEADER, "user-1")
        .build()

    @Test
    fun `rejects every request with 503 when the secret is not configured`() {
        val exchange = MockServerWebExchange.from(request())

        InternalAuthFilter("").filter(exchange, chain).block()

        verify(exactly = 0) { chain.filter(any()) }
        assertEquals(HttpStatus.SERVICE_UNAVAILABLE, exchange.response.statusCode)
        assertContains(exchange.response.bodyAsString.block()!!, "\"code\":\"SERVICE_UNAVAILABLE\"")
    }

    @Test
    fun `adds an HMAC-SHA256 of request id, tenant and user keyed with the secret`() {
        val forwarded = slot<ServerWebExchange>()
        every { chain.filter(capture(forwarded)) } returns Mono.empty()

        InternalAuthFilter("s3cret").filter(MockServerWebExchange.from(request()), chain).block()

        val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec("s3cret".toByteArray(), "HmacSHA256")) }
        val expected = mac.doFinal("req-1:acme:user-1".toByteArray()).joinToString("") { "%02x".format(it) }
        assertEquals(expected, forwarded.captured.request.headers.getFirst(InternalAuthFilter.INTERNAL_TOKEN_HEADER))
    }
}
