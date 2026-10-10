package com.docintel.gateway.filter

import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.cloud.gateway.filter.GatewayFilterChain
import org.springframework.http.HttpStatus
import org.springframework.http.server.reactive.ServerHttpRequest
import org.springframework.mock.http.server.reactive.MockServerHttpRequest
import org.springframework.mock.web.server.MockServerWebExchange
import org.springframework.security.core.context.ReactiveSecurityContextHolder
import org.springframework.security.oauth2.jwt.Jwt
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken
import org.springframework.web.server.ServerWebExchange
import reactor.core.publisher.Mono
import kotlin.test.assertContains
import kotlin.test.assertEquals
import kotlin.test.assertNull

/**
 * TenantFilter turns the gateway-validated JWT into the identity headers backends
 * trust. Client-supplied identity headers must never reach a backend.
 */
class TenantFilterTest {

    private lateinit var filter: TenantFilter
    private lateinit var chain: GatewayFilterChain
    private lateinit var forwarded: io.mockk.CapturingSlot<ServerWebExchange>

    @BeforeEach
    fun setUp() {
        filter = TenantFilter()
        chain = mockk()
        forwarded = slot()
        every { chain.filter(capture(forwarded)) } returns Mono.empty()
    }

    private fun jwt(vararg claims: Pair<String, Any>): Jwt {
        val builder = Jwt.withTokenValue("token").header("alg", "RS256")
        claims.forEach { (name, value) -> builder.claim(name, value) }
        if (claims.none { it.first == "sub" }) builder.subject("user-1")
        return builder.build()
    }

    private fun runWithJwt(request: MockServerHttpRequest, token: Jwt): MockServerWebExchange {
        val exchange = MockServerWebExchange.from(request)
        filter.filter(exchange, chain)
            .contextWrite(ReactiveSecurityContextHolder.withAuthentication(JwtAuthenticationToken(token)))
            .block()
        return exchange
    }

    private fun runWithoutSecurityContext(request: MockServerHttpRequest): MockServerWebExchange {
        val exchange = MockServerWebExchange.from(request)
        filter.filter(exchange, chain).block()
        return exchange
    }

    private fun forwardedRequest(): ServerHttpRequest = forwarded.captured.request

    private fun get() = MockServerHttpRequest.get("/api/v1/documents")

    @Test
    fun `uses the tenant_id claim as X-Tenant-Id`() {
        runWithJwt(get().build(), jwt("tenant_id" to "acme"))

        assertEquals("acme", forwardedRequest().headers.getFirst(TenantFilter.TENANT_HEADER))
    }

    @Test
    fun `overwrites a client-supplied X-Tenant-Id with the claim`() {
        runWithJwt(get().header(TenantFilter.TENANT_HEADER, "victim").build(), jwt("tenant_id" to "acme"))

        assertEquals(listOf("acme"), forwardedRequest().headers[TenantFilter.TENANT_HEADER])
    }

    @Test
    fun `keeps tenant ids with dots, dashes and underscores verbatim`() {
        runWithJwt(get().build(), jwt("tenant_id" to "tenant_with-special.chars"))

        assertEquals("tenant_with-special.chars", forwardedRequest().headers.getFirst(TenantFilter.TENANT_HEADER))
    }

    @Test
    fun `rejects a JWT without a tenant_id claim with 403 TENANT_CLAIM_MISSING`() {
        val exchange = runWithJwt(get().header(TenantFilter.TENANT_HEADER, "acme").build(), jwt())

        verify(exactly = 0) { chain.filter(any()) }
        assertEquals(HttpStatus.FORBIDDEN, exchange.response.statusCode)
        assertContains(exchange.response.bodyAsString.block()!!, "\"code\":\"TENANT_CLAIM_MISSING\"")
    }

    @Test
    fun `rejects a JWT whose tenant_id claim is blank`() {
        val exchange = runWithJwt(get().build(), jwt("tenant_id" to "  "))

        verify(exactly = 0) { chain.filter(any()) }
        assertEquals(HttpStatus.FORBIDDEN, exchange.response.statusCode)
    }

    @Test
    fun `sets user headers from the sub, email, name, roles and clearance claims`() {
        runWithJwt(
            get().build(),
            jwt(
                "tenant_id" to "acme",
                "sub" to "user-42",
                "email" to "ana@acme.test",
                "name" to "Ana",
                "roles" to listOf("documents:r", "query:execute"),
                "clearance" to "confidential",
            ),
        )

        val headers = forwardedRequest().headers
        assertEquals("user-42", headers.getFirst(TenantFilter.USER_ID_HEADER))
        assertEquals("ana@acme.test", headers.getFirst(TenantFilter.USER_EMAIL_HEADER))
        assertEquals("Ana", headers.getFirst(TenantFilter.USER_NAME_HEADER))
        assertEquals("documents:r,query:execute", headers.getFirst(TenantFilter.USER_ROLES_HEADER))
        assertEquals("confidential", headers.getFirst(TenantFilter.USER_CLEARANCE_HEADER))
    }

    @Test
    fun `accepts roles as a comma-separated string claim`() {
        runWithJwt(get().build(), jwt("tenant_id" to "acme", "roles" to "documents:r, query:execute"))

        assertEquals("documents:r,query:execute", forwardedRequest().headers.getFirst(TenantFilter.USER_ROLES_HEADER))
    }

    @Test
    fun `drops client-supplied identity headers the JWT has no claim for`() {
        // A machine token has no email or name claim; spoofed values must not pass through.
        val request = get()
            .header(TenantFilter.USER_EMAIL_HEADER, "admin@victim.test")
            .header(TenantFilter.USER_NAME_HEADER, "Admin")
            .build()

        runWithJwt(request, jwt("tenant_id" to "acme"))

        assertNull(forwardedRequest().headers.getFirst(TenantFilter.USER_EMAIL_HEADER))
        assertNull(forwardedRequest().headers.getFirst(TenantFilter.USER_NAME_HEADER))
    }

    @Test
    fun `replaces every client-supplied identity header with the claim value`() {
        val request = get()
            .header(TenantFilter.USER_ID_HEADER, "spoofed")
            .header(TenantFilter.USER_ROLES_HEADER, "platform:admin")
            .header(TenantFilter.USER_CLEARANCE_HEADER, "restricted")
            .build()

        runWithJwt(request, jwt("tenant_id" to "acme", "sub" to "user-42"))

        val headers = forwardedRequest().headers
        assertEquals(listOf("user-42"), headers[TenantFilter.USER_ID_HEADER])
        assertEquals(listOf(""), headers[TenantFilter.USER_ROLES_HEADER])
        assertEquals(listOf(TenantFilter.DEFAULT_CLEARANCE), headers[TenantFilter.USER_CLEARANCE_HEADER])
    }

    @Test
    fun `routes a request without a security context as tenant default with no user identity`() {
        // Only the "dev" profile, which permits every request, reaches this path.
        val request = get()
            .header(TenantFilter.TENANT_HEADER, "acme")
            .header(TenantFilter.USER_ID_HEADER, "spoofed")
            .build()

        runWithoutSecurityContext(request)

        val headers = forwardedRequest().headers
        assertEquals(TenantFilter.DEFAULT_TENANT, headers.getFirst(TenantFilter.TENANT_HEADER))
        assertNull(headers.getFirst(TenantFilter.USER_ID_HEADER))
    }

    @Test
    fun `preserves headers that are not identity headers`() {
        val request = get()
            .header("Authorization", "Bearer token123")
            .header("Content-Type", "application/json")
            .build()

        runWithJwt(request, jwt("tenant_id" to "acme"))

        assertEquals("Bearer token123", forwardedRequest().headers.getFirst("Authorization"))
        assertEquals("application/json", forwardedRequest().headers.getFirst("Content-Type"))
    }

    @Test
    fun `runs before InternalAuthFilter and OPA`() {
        assertEquals(-100, filter.order)
    }
}
