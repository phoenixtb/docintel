package com.docintel.gateway

import com.docintel.gateway.support.GatewayTestInfra
import com.github.tomakehurst.wiremock.client.WireMock.aResponse
import com.github.tomakehurst.wiremock.client.WireMock.absent
import com.github.tomakehurst.wiremock.client.WireMock.anyRequestedFor
import com.github.tomakehurst.wiremock.client.WireMock.equalTo
import com.github.tomakehurst.wiremock.client.WireMock.get
import com.github.tomakehurst.wiremock.client.WireMock.getRequestedFor
import com.github.tomakehurst.wiremock.client.WireMock.post
import com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo
import com.github.tomakehurst.wiremock.client.WireMock.urlPathMatching
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.http.MediaType
import org.springframework.test.context.ActiveProfiles
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.reactive.server.WebTestClient
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import kotlin.test.assertEquals

/**
 * The gateway end to end, through the production security chain and route table:
 * real RS256 JWTs verified against a JWKS served by WireMock, OPA stubbed on WireMock,
 * a real Redis for the quota filter, and WireMock standing in for the backends.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class GatewayIntegrationTest {

    @Autowired
    private lateinit var webTestClient: WebTestClient

    private val wireMock get() = GatewayTestInfra.wireMock

    companion object {
        @JvmStatic
        @DynamicPropertySource
        fun configureProperties(registry: DynamicPropertyRegistry) {
            GatewayTestInfra.registerProperties(registry)
        }
    }

    @BeforeEach
    fun setUp() {
        GatewayTestInfra.resetStubs()
    }

    private fun stubDocumentList() {
        wireMock.stubFor(
            get(urlPathEqualTo("/internal/documents")).willReturn(
                aResponse()
                    .withStatus(200)
                    .withHeader("Content-Type", "application/json")
                    .withBody("""{"content": [], "totalElements": 0}""")
            )
        )
    }

    private fun hmac(message: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(GatewayTestInfra.INTERNAL_SECRET.toByteArray(), "HmacSHA256"))
        return mac.doFinal(message.toByteArray()).joinToString("") { "%02x".format(it) }
    }

    @Test
    fun `routes a document list request to document-service`() {
        stubDocumentList()

        webTestClient.get()
            .uri("/api/v1/documents")
            .headers { it.setBearerAuth(GatewayTestInfra.token()) }
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.content").isArray
    }

    @Test
    fun `routes a document get request with its path parameter`() {
        val docId = "123e4567-e89b-12d3-a456-426614174000"
        wireMock.stubFor(
            get(urlPathEqualTo("/internal/documents/$docId")).willReturn(
                aResponse()
                    .withStatus(200)
                    .withHeader("Content-Type", "application/json")
                    .withBody("""{"id": "$docId", "filename": "test.txt"}""")
            )
        )

        webTestClient.get()
            .uri("/api/v1/documents/$docId")
            .headers { it.setBearerAuth(GatewayTestInfra.token()) }
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.id").isEqualTo(docId)
    }

    @Test
    fun `routes a query to rag-service`() {
        wireMock.stubFor(
            post(urlPathEqualTo("/query")).willReturn(
                aResponse()
                    .withStatus(200)
                    .withHeader("Content-Type", "application/json")
                    .withBody("""{"answer": "Test answer", "sources": []}""")
            )
        )

        webTestClient.post()
            .uri("/api/v1/query")
            .headers { it.setBearerAuth(GatewayTestInfra.token()) }
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue("""{"question": "What is the leave policy?"}""")
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.answer").isEqualTo("Test answer")
    }

    @Test
    fun `passes query parameters to the downstream service`() {
        stubDocumentList()

        webTestClient.get()
            .uri("/api/v1/documents?status=COMPLETED&page=2")
            .headers { it.setBearerAuth(GatewayTestInfra.token()) }
            .exchange()
            .expectStatus().isOk

        wireMock.verify(
            getRequestedFor(urlPathEqualTo("/internal/documents"))
                .withQueryParam("status", equalTo("COMPLETED"))
                .withQueryParam("page", equalTo("2"))
        )
    }

    @Test
    fun `streams a query response as server-sent events`() {
        wireMock.stubFor(
            post(urlPathEqualTo("/query/stream")).willReturn(
                aResponse()
                    .withStatus(200)
                    .withHeader("Content-Type", "text/event-stream")
                    .withBody("data: {\"token\": \"Hello\"}\n\n")
            )
        )

        webTestClient.post()
            .uri("/api/v1/query/stream")
            .headers { it.setBearerAuth(GatewayTestInfra.token()) }
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue("""{"question": "Hi"}""")
            .exchange()
            .expectStatus().isOk
            .expectHeader().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM)
    }

    @Test
    fun `sends the tenant from the JWT claim, not from a client header`() {
        stubDocumentList()

        webTestClient.get()
            .uri("/api/v1/documents")
            .headers {
                it.setBearerAuth(GatewayTestInfra.token(tenantId = "acme"))
                it.set("X-Tenant-Id", "victim")
            }
            .exchange()
            .expectStatus().isOk

        wireMock.verify(
            getRequestedFor(urlPathEqualTo("/internal/documents"))
                .withHeader("X-Tenant-Id", equalTo("acme"))
        )
    }

    @Test
    fun `does not forward a client-supplied X-User-Email the token has no claim for`() {
        stubDocumentList()

        webTestClient.get()
            .uri("/api/v1/documents")
            .headers {
                it.setBearerAuth(GatewayTestInfra.token())
                it.set("X-User-Email", "admin@victim.test")
            }
            .exchange()
            .expectStatus().isOk

        wireMock.verify(
            getRequestedFor(urlPathEqualTo("/internal/documents"))
                .withHeader("X-User-Email", absent())
        )
    }

    @Test
    fun `signs forwarded requests with an HMAC of request id, tenant and user`() {
        stubDocumentList()

        webTestClient.get()
            .uri("/api/v1/documents")
            .headers {
                it.setBearerAuth(GatewayTestInfra.token(tenantId = "acme", subject = "user-7"))
                it.set("X-Request-Id", "req-123")
            }
            .exchange()
            .expectStatus().isOk

        val forwarded = wireMock.findAll(getRequestedFor(urlPathEqualTo("/internal/documents"))).single()
        assertEquals(hmac("req-123:acme:user-7"), forwarded.getHeader("X-Internal-Service-Token"))
    }

    @Test
    fun `rejects a request without a bearer token with 401`() {
        webTestClient.get()
            .uri("/api/v1/documents")
            .exchange()
            .expectStatus().isUnauthorized
            .expectBody()
            .jsonPath("$.error.code").isEqualTo("UNAUTHORIZED")

        wireMock.verify(0, anyRequestedFor(urlPathMatching("/internal/.*")))
    }

    @Test
    fun `rejects a token whose signature does not match the published key with 401`() {
        webTestClient.get()
            .uri("/api/v1/documents")
            .headers { it.setBearerAuth(GatewayTestInfra.token(signedBy = GatewayTestInfra.foreignKey)) }
            .exchange()
            .expectStatus().isUnauthorized

        wireMock.verify(0, anyRequestedFor(urlPathMatching("/internal/.*")))
    }

    @Test
    fun `rejects a token without a tenant_id claim with 403 before reaching any backend`() {
        stubDocumentList()

        webTestClient.get()
            .uri("/api/v1/documents")
            .headers { it.setBearerAuth(GatewayTestInfra.token(tenantId = null)) }
            .exchange()
            .expectStatus().isForbidden
            .expectBody()
            .jsonPath("$.error.code").isEqualTo("TENANT_CLAIM_MISSING")

        wireMock.verify(0, anyRequestedFor(urlPathMatching("/internal/.*")))
    }

    @Test
    fun `returns 403 when OPA denies the request`() {
        // A tenant no other test uses, so no cached OPA decision applies.
        GatewayTestInfra.stubOpa(allow = false)
        stubDocumentList()

        webTestClient.get()
            .uri("/api/v1/documents")
            .headers { it.setBearerAuth(GatewayTestInfra.token(tenantId = "opa-denied")) }
            .exchange()
            .expectStatus().isForbidden
            .expectBody()
            .jsonPath("$.error.code").isEqualTo("FORBIDDEN")

        wireMock.verify(0, anyRequestedFor(urlPathMatching("/internal/.*")))
    }

    @Test
    fun `passes a downstream 404 through`() {
        wireMock.stubFor(
            get(urlPathEqualTo("/internal/documents/nonexistent")).willReturn(aResponse().withStatus(404))
        )

        webTestClient.get()
            .uri("/api/v1/documents/nonexistent")
            .headers { it.setBearerAuth(GatewayTestInfra.token()) }
            .exchange()
            .expectStatus().isNotFound
    }

    @Test
    fun `passes a downstream 500 through`() {
        wireMock.stubFor(
            get(urlPathEqualTo("/internal/documents")).willReturn(
                aResponse().withStatus(500).withBody("""{"error": "Internal error"}""")
            )
        )

        webTestClient.get()
            .uri("/api/v1/documents")
            .headers { it.setBearerAuth(GatewayTestInfra.token()) }
            .exchange()
            .expectStatus().is5xxServerError
    }
}
