package com.docintel.gateway.controller

import com.docintel.gateway.support.GatewayTestInfra
import com.github.tomakehurst.wiremock.client.WireMock.aResponse
import com.github.tomakehurst.wiremock.client.WireMock.get
import com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.boot.test.autoconfigure.web.reactive.AutoConfigureWebTestClient
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.test.context.ActiveProfiles
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.springframework.test.web.reactive.server.WebTestClient

/**
 * /health reports the gateway's own dependencies: OPA (stubbed on WireMock) and Redis (real).
 */
// "dev" activates the permit-all security chain: /health is not on the JWT chain's
// permitAll list (only /actuator/health and /api/v1/health are).
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@AutoConfigureWebTestClient
@ActiveProfiles("test", "dev")
class HealthControllerTest {

    @Autowired
    private lateinit var webTestClient: WebTestClient

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

    private fun stubOpaHealthy() {
        GatewayTestInfra.wireMock.stubFor(get(urlPathEqualTo("/health")).willReturn(aResponse().withStatus(200)))
    }

    @Test
    fun `reports healthy when OPA and Redis are reachable`() {
        stubOpaHealthy()

        webTestClient.get()
            .uri("/health")
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.status").isEqualTo("healthy")
            .jsonPath("$.dependencies.opa").isEqualTo("connected")
            .jsonPath("$.dependencies.redis").isEqualTo("connected")
    }

    @Test
    fun `reports degraded with 200 when OPA is unhealthy`() {
        // No /health stub: WireMock answers 404.
        webTestClient.get()
            .uri("/health")
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.status").isEqualTo("degraded")
            .jsonPath("$.dependencies.opa").isEqualTo("unreachable")
            .jsonPath("$.dependencies.redis").isEqualTo("connected")
    }

    @Test
    fun `identifies the service and its version`() {
        stubOpaHealthy()

        webTestClient.get()
            .uri("/health")
            .exchange()
            .expectStatus().isOk
            .expectBody()
            .jsonPath("$.service").isEqualTo("api-gateway")
            .jsonPath("$.version").exists()
    }
}
