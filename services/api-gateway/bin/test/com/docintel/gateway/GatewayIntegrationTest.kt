package com.docintel.gateway

import com.github.tomakehurst.wiremock.WireMockServer
import com.github.tomakehurst.wiremock.client.WireMock.*
import com.github.tomakehurst.wiremock.core.WireMockConfiguration
import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.web.server.LocalServerPort
import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType
import org.springframework.test.context.ActiveProfiles
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.reactive.server.WebTestClient
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Integration tests for API Gateway routing.
 * Uses WireMock to mock downstream services.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class GatewayIntegrationTest {

    @LocalServerPort
    private var port: Int = 0

    @Autowired
    private lateinit var webTestClient: WebTestClient

    companion object {
        private lateinit var wireMockServer: WireMockServer

        @JvmStatic
        @BeforeAll
        fun startWireMock() {
            wireMockServer = WireMockServer(WireMockConfiguration.options().dynamicPort())
            wireMockServer.start()
        }

        @JvmStatic
        @AfterAll
        fun stopWireMock() {
            wireMockServer.stop()
        }

        @JvmStatic
        @DynamicPropertySource
        fun configureProperties(registry: DynamicPropertyRegistry) {
            registry.add("wiremock.server.port") { wireMockServer.port() }
            
            // Configure gateway routes to use WireMock
            registry.add("spring.cloud.gateway.routes[0].id") { "document-service" }
            registry.add("spring.cloud.gateway.routes[0].uri") { "http://localhost:${wireMockServer.port()}" }
            registry.add("spring.cloud.gateway.routes[0].predicates[0]") { "Path=/api/v1/documents, /api/v1/documents/**" }
            registry.add("spring.cloud.gateway.routes[0].filters[0]") { "RewritePath=/api/v1/documents(?<segment>/?.*), /internal/documents\${segment}" }
            
            registry.add("spring.cloud.gateway.routes[1].id") { "rag-service" }
            registry.add("spring.cloud.gateway.routes[1].uri") { "http://localhost:${wireMockServer.port()}" }
            registry.add("spring.cloud.gateway.routes[1].predicates[0]") { "Path=/api/v1/query/**" }
            registry.add("spring.cloud.gateway.routes[1].filters[0]") { "RewritePath=/api/v1/query(?<segment>/?.*), /query\${segment}" }
            
            // Disable security for tests
            registry.add("spring.security.oauth2.resourceserver.jwt.issuer-uri") { "" }
        }
    }

    @BeforeEach
    fun setUp() {
        wireMockServer.resetAll()
    }

    @Test
    fun `should route document list request to document service`() {
        // Given
        wireMockServer.stubFor(
            get(urlPathEqualTo("/internal/documents"))
                .willReturn(
                    aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""{"content": [], "totalElements": 0}""")
                )
        )

        // When & Then
        webTestClient.get()
            .uri("/api/v1/documents")
            .header("X-Tenant-Id", "test-tenant")
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.content").isArray

        // Verify WireMock received the request
        wireMockServer.verify(
            getRequestedFor(urlPathEqualTo("/internal/documents"))
                .withHeader("X-Tenant-Id", equalTo("test-tenant"))
        )
    }

    @Test
    fun `should route document get request with path parameter`() {
        // Given
        val docId = "123e4567-e89b-12d3-a456-426614174000"
        wireMockServer.stubFor(
            get(urlPathEqualTo("/internal/documents/$docId"))
                .willReturn(
                    aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""{"id": "$docId", "filename": "test.txt"}""")
                )
        )

        // When & Then
        webTestClient.get()
            .uri("/api/v1/documents/$docId")
            .header("X-Tenant-Id", "test-tenant")
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.id").isEqualTo(docId)
    }

    @Test
    fun `should route query request to RAG service`() {
        // Given
        wireMockServer.stubFor(
            post(urlPathEqualTo("/query"))
                .willReturn(
                    aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "application/json")
                        .withBody("""{"answer": "Test answer", "sources": []}""")
                )
        )

        // When & Then
        webTestClient.post()
            .uri("/api/v1/query")
            .header("X-Tenant-Id", "test-tenant")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue("""{"question": "What is the leave policy?", "tenant_id": "test-tenant"}""")
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.answer").isEqualTo("Test answer")
    }

    @Test
    fun `should add tenant header to downstream requests`() {
        // Given
        wireMockServer.stubFor(
            get(urlPathEqualTo("/internal/documents"))
                .willReturn(aResponse().withStatus(200).withBody("{}"))
        )

        // When
        webTestClient.get()
            .uri("/api/v1/documents")
            .header("X-Tenant-Id", "my-custom-tenant")
            .exchange()
            .expectStatus().isOk

        // Then
        wireMockServer.verify(
            getRequestedFor(urlPathEqualTo("/internal/documents"))
                .withHeader("X-Tenant-Id", equalTo("my-custom-tenant"))
        )
    }

    @Test
    fun `should use default tenant when not provided`() {
        // Given
        wireMockServer.stubFor(
            get(urlPathEqualTo("/internal/documents"))
                .willReturn(aResponse().withStatus(200).withBody("{}"))
        )

        // When
        webTestClient.get()
            .uri("/api/v1/documents")
            .exchange()
            .expectStatus().isOk

        // Then
        wireMockServer.verify(
            getRequestedFor(urlPathEqualTo("/internal/documents"))
                .withHeader("X-Tenant-Id", equalTo("default"))
        )
    }

    @Test
    fun `should include rate limit headers in response`() {
        // Given
        wireMockServer.stubFor(
            get(urlPathEqualTo("/internal/documents"))
                .willReturn(aResponse().withStatus(200).withBody("{}"))
        )

        // When & Then
        webTestClient.get()
            .uri("/api/v1/documents")
            .header("X-Tenant-Id", "rate-limit-test")
            .exchange()
            .expectStatus().isOk
            .expectHeader().exists("X-RateLimit-Limit")
            .expectHeader().exists("X-RateLimit-Remaining")
    }

    @Test
    fun `should handle downstream service error`() {
        // Given
        wireMockServer.stubFor(
            get(urlPathEqualTo("/internal/documents"))
                .willReturn(aResponse().withStatus(500).withBody("""{"error": "Internal error"}"""))
        )

        // When & Then
        webTestClient.get()
            .uri("/api/v1/documents")
            .header("X-Tenant-Id", "test-tenant")
            .exchange()
            .expectStatus().is5xxServerError
    }

    @Test
    fun `should handle downstream service not found`() {
        // Given
        wireMockServer.stubFor(
            get(urlPathEqualTo("/internal/documents/nonexistent"))
                .willReturn(aResponse().withStatus(404))
        )

        // When & Then
        webTestClient.get()
            .uri("/api/v1/documents/nonexistent")
            .header("X-Tenant-Id", "test-tenant")
            .exchange()
            .expectStatus().isNotFound
    }

    @Test
    fun `should pass query parameters to downstream service`() {
        // Given
        wireMockServer.stubFor(
            get(urlPathEqualTo("/internal/documents"))
                .withQueryParam("tenant_id", equalTo("test-tenant"))
                .withQueryParam("status", equalTo("COMPLETED"))
                .willReturn(aResponse().withStatus(200).withBody("{}"))
        )

        // When
        webTestClient.get()
            .uri("/api/v1/documents?tenant_id=test-tenant&status=COMPLETED")
            .exchange()
            .expectStatus().isOk

        // Then
        wireMockServer.verify(
            getRequestedFor(urlPathEqualTo("/internal/documents"))
                .withQueryParam("tenant_id", equalTo("test-tenant"))
                .withQueryParam("status", equalTo("COMPLETED"))
        )
    }

    @Test
    fun `should handle streaming query response`() {
        // Given
        wireMockServer.stubFor(
            post(urlPathEqualTo("/query/stream"))
                .willReturn(
                    aResponse()
                        .withStatus(200)
                        .withHeader("Content-Type", "text/event-stream")
                        .withBody("data: {\"token\": \"Hello\"}\n\n")
                )
        )

        // When & Then
        webTestClient.post()
            .uri("/api/v1/query/stream")
            .header("X-Tenant-Id", "test-tenant")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue("""{"question": "Hi"}""")
            .exchange()
            .expectStatus().isOk
            .expectHeader().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM)
    }
}
