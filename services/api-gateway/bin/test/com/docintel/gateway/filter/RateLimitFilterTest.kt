package com.docintel.gateway.filter

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.http.HttpStatus
import org.springframework.mock.http.server.reactive.MockServerHttpRequest
import org.springframework.mock.web.server.MockServerWebExchange
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Unit tests for RateLimitFilter.
 */
class RateLimitFilterTest {

    private lateinit var filter: RateLimitFilter
    private lateinit var chain: GatewayFilterChain

    @BeforeEach
    fun setUp() {
        filter = RateLimitFilter()
        chain = mockk()
        every { chain.filter(any()) } returns Mono.empty()
    }

    @Test
    fun `should allow request within rate limit`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/documents")
            .header(TenantFilter.TENANT_HEADER, "test-tenant")
            .build()
        val exchange = MockServerWebExchange.from(request)

        // When
        filter.filter(exchange, chain).block()

        // Then
        // Should not be rate limited
        assertNotNull(exchange.response.headers.getFirst("X-RateLimit-Limit"))
        assertTrue(exchange.response.headers.getFirst("X-RateLimit-Remaining")?.toInt()!! > 0)
    }

    @Test
    fun `should apply lower rate limit for query endpoints`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/query")
            .header(TenantFilter.TENANT_HEADER, "test-tenant")
            .build()
        val exchange = MockServerWebExchange.from(request)

        // When
        filter.filter(exchange, chain).block()

        // Then
        val limit = exchange.response.headers.getFirst("X-RateLimit-Limit")?.toInt()
        assertEquals(RateLimitFilter.QUERY_LIMIT, limit)
    }

    @Test
    fun `should apply default rate limit for non-query endpoints`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/documents")
            .header(TenantFilter.TENANT_HEADER, "test-tenant")
            .build()
        val exchange = MockServerWebExchange.from(request)

        // When
        filter.filter(exchange, chain).block()

        // Then
        val limit = exchange.response.headers.getFirst("X-RateLimit-Limit")?.toInt()
        assertEquals(RateLimitFilter.DEFAULT_LIMIT, limit)
    }

    @Test
    fun `should return 429 when rate limit exceeded`() {
        // Given
        val tenantId = "rate-limit-test-${System.currentTimeMillis()}"
        
        // Exhaust query rate limit (20 requests)
        repeat(RateLimitFilter.QUERY_LIMIT + 1) { i ->
            val request = MockServerHttpRequest.post("/api/v1/query/stream")
                .header(TenantFilter.TENANT_HEADER, tenantId)
                .build()
            val exchange = MockServerWebExchange.from(request)
            
            filter.filter(exchange, chain).block()
            
            if (i >= RateLimitFilter.QUERY_LIMIT) {
                // Should be rate limited
                assertEquals(HttpStatus.TOO_MANY_REQUESTS, exchange.response.statusCode)
                assertEquals("0", exchange.response.headers.getFirst("X-RateLimit-Remaining"))
                assertNotNull(exchange.response.headers.getFirst("Retry-After"))
            }
        }
    }

    @Test
    fun `should track rate limits per tenant`() {
        // Given
        val tenant1 = "tenant-1-${System.currentTimeMillis()}"
        val tenant2 = "tenant-2-${System.currentTimeMillis()}"

        // When - Make requests from tenant1
        repeat(5) {
            val request = MockServerHttpRequest.get("/api/v1/documents")
                .header(TenantFilter.TENANT_HEADER, tenant1)
                .build()
            val exchange = MockServerWebExchange.from(request)
            filter.filter(exchange, chain).block()
        }

        // Then - Tenant2 should have full quota
        val request = MockServerHttpRequest.get("/api/v1/documents")
            .header(TenantFilter.TENANT_HEADER, tenant2)
            .build()
        val exchange = MockServerWebExchange.from(request)
        filter.filter(exchange, chain).block()

        val remaining = exchange.response.headers.getFirst("X-RateLimit-Remaining")?.toInt()
        assertEquals(RateLimitFilter.DEFAULT_LIMIT - 1, remaining)
    }

    @Test
    fun `should use default tenant when header missing`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/documents").build()
        val exchange = MockServerWebExchange.from(request)

        // When
        filter.filter(exchange, chain).block()

        // Then - Should not fail, uses default tenant
        assertNotNull(exchange.response.headers.getFirst("X-RateLimit-Limit"))
    }

    @Test
    fun `should have correct order priority`() {
        // RateLimitFilter should run after TenantFilter
        assertTrue(filter.order > TenantFilter().order)
        assertEquals(-50, filter.order)
    }

    @Test
    fun `should separate query and default rate limits`() {
        // Given
        val tenantId = "separate-limits-${System.currentTimeMillis()}"

        // Exhaust query limit
        repeat(RateLimitFilter.QUERY_LIMIT) {
            val request = MockServerHttpRequest.post("/api/v1/query")
                .header(TenantFilter.TENANT_HEADER, tenantId)
                .build()
            val exchange = MockServerWebExchange.from(request)
            filter.filter(exchange, chain).block()
        }

        // Then - Default endpoints should still work
        val docRequest = MockServerHttpRequest.get("/api/v1/documents")
            .header(TenantFilter.TENANT_HEADER, tenantId)
            .build()
        val docExchange = MockServerWebExchange.from(docRequest)
        filter.filter(docExchange, chain).block()

        // Should not be rate limited for documents
        val remaining = docExchange.response.headers.getFirst("X-RateLimit-Remaining")?.toInt()
        assertTrue(remaining!! > 0)
    }
}
