package com.docintel.document.filter

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import org.springframework.mock.web.MockFilterChain
import org.springframework.mock.web.MockHttpServletRequest
import org.springframework.mock.web.MockHttpServletResponse

class InternalAuthFilterTest {

    private val secret = "test-secret"
    private val filter = InternalAuthFilter(secret)

    @Test
    fun `service-only from-path is rejected with only X-User-Id`() {
        val request = MockHttpServletRequest("POST", "/internal/documents/from-path")
        request.addHeader("X-User-Id", "user-1")
        val response = MockHttpServletResponse()

        filter.doFilter(request, response, MockFilterChain())

        assertEquals(403, response.status)
    }

    @Test
    fun `service-only chunks bulk is rejected with only X-User-Id`() {
        val request = MockHttpServletRequest("POST", "/internal/documents/11111111-1111-1111-1111-111111111111/chunks/bulk")
        request.addHeader("X-User-Id", "user-1")
        val response = MockHttpServletResponse()

        filter.doFilter(request, response, MockFilterChain())

        assertEquals(403, response.status)
    }

    @Test
    fun `service-only data-sources is rejected with only X-User-Id`() {
        val request = MockHttpServletRequest("GET", "/internal/documents/data-sources")
        request.addHeader("X-User-Id", "user-1")
        val response = MockHttpServletResponse()

        filter.doFilter(request, response, MockFilterChain())

        assertEquals(403, response.status)
    }

    @Test
    fun `service-only from-path is accepted with valid HMAC token`() {
        val token = HmacUtils.compute(":tenant-a:", secret)
        val request = MockHttpServletRequest("POST", "/internal/documents/from-path")
        request.addHeader("X-Internal-Service-Token", token)
        request.addHeader("X-Tenant-Id", "tenant-a")
        val response = MockHttpServletResponse()
        val chain = MockFilterChain()

        filter.doFilter(request, response, chain)

        assertEquals(200, response.status)
    }

    @Test
    fun `regular document route still allows plain X-User-Id`() {
        val request = MockHttpServletRequest("GET", "/internal/documents/some-id")
        request.addHeader("X-User-Id", "user-1")
        val response = MockHttpServletResponse()
        val chain = MockFilterChain()

        filter.doFilter(request, response, chain)

        assertEquals(200, response.status)
    }

    @Test
    fun `actuator and openapi paths are open`() {
        for (path in listOf("/actuator/health", "/v3/api-docs", "/swagger-ui/index.html")) {
            val request = MockHttpServletRequest("GET", path)
            val response = MockHttpServletResponse()
            val chain = MockFilterChain()

            filter.doFilter(request, response, chain)

            assertEquals(200, response.status, "expected $path to be open")
        }
    }
}
