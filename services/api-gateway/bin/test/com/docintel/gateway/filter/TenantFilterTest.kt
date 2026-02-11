package com.docintel.gateway.filter

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.http.HttpHeaders
import org.springframework.mock.http.server.reactive.MockServerHttpRequest
import org.springframework.mock.web.server.MockServerWebExchange
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Unit tests for TenantFilter.
 */
class TenantFilterTest {

    private lateinit var filter: TenantFilter
    private lateinit var chain: GatewayFilterChain

    @BeforeEach
    fun setUp() {
        filter = TenantFilter()
        chain = mockk()
    }

    @Test
    fun `should extract tenant from header`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/documents")
            .header(TenantFilter.TENANT_HEADER, "custom-tenant")
            .build()
        val exchange = MockServerWebExchange.from(request)
        
        val capturedExchange = slot<ServerWebExchange>()
        every { chain.filter(capture(capturedExchange)) } returns Mono.empty()

        // When
        filter.filter(exchange, chain).block()

        // Then
        verify { chain.filter(any()) }
        val mutatedRequest = capturedExchange.captured.request
        assertEquals("custom-tenant", mutatedRequest.headers.getFirst(TenantFilter.TENANT_HEADER))
    }

    @Test
    fun `should use default tenant when header is missing`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/documents").build()
        val exchange = MockServerWebExchange.from(request)
        
        val capturedExchange = slot<ServerWebExchange>()
        every { chain.filter(capture(capturedExchange)) } returns Mono.empty()

        // When
        filter.filter(exchange, chain).block()

        // Then
        val mutatedRequest = capturedExchange.captured.request
        assertEquals(TenantFilter.DEFAULT_TENANT, mutatedRequest.headers.getFirst(TenantFilter.TENANT_HEADER))
    }

    @Test
    fun `should use default tenant when header is blank`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/documents")
            .header(TenantFilter.TENANT_HEADER, "")
            .build()
        val exchange = MockServerWebExchange.from(request)
        
        val capturedExchange = slot<ServerWebExchange>()
        every { chain.filter(capture(capturedExchange)) } returns Mono.empty()

        // When
        filter.filter(exchange, chain).block()

        // Then
        val mutatedRequest = capturedExchange.captured.request
        assertEquals(TenantFilter.DEFAULT_TENANT, mutatedRequest.headers.getFirst(TenantFilter.TENANT_HEADER))
    }

    @Test
    fun `should preserve other headers`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/documents")
            .header(TenantFilter.TENANT_HEADER, "my-tenant")
            .header("Authorization", "Bearer token123")
            .header("Content-Type", "application/json")
            .build()
        val exchange = MockServerWebExchange.from(request)
        
        val capturedExchange = slot<ServerWebExchange>()
        every { chain.filter(capture(capturedExchange)) } returns Mono.empty()

        // When
        filter.filter(exchange, chain).block()

        // Then
        val mutatedRequest = capturedExchange.captured.request
        assertEquals("Bearer token123", mutatedRequest.headers.getFirst("Authorization"))
        assertEquals("application/json", mutatedRequest.headers.getFirst("Content-Type"))
    }

    @Test
    fun `should have correct order priority`() {
        // TenantFilter should run early (negative order)
        assertTrue(filter.order < 0)
        assertEquals(-100, filter.order)
    }

    @Test
    fun `should handle tenant with special characters`() {
        // Given
        val request = MockServerHttpRequest.get("/api/v1/documents")
            .header(TenantFilter.TENANT_HEADER, "tenant_with-special.chars")
            .build()
        val exchange = MockServerWebExchange.from(request)
        
        val capturedExchange = slot<ServerWebExchange>()
        every { chain.filter(capture(capturedExchange)) } returns Mono.empty()

        // When
        filter.filter(exchange, chain).block()

        // Then
        val mutatedRequest = capturedExchange.captured.request
        assertEquals("tenant_with-special.chars", mutatedRequest.headers.getFirst(TenantFilter.TENANT_HEADER))
    }
}
