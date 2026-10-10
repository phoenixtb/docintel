package com.docintel.gateway.support

import com.github.tomakehurst.wiremock.WireMockServer
import com.github.tomakehurst.wiremock.client.WireMock.aResponse
import com.github.tomakehurst.wiremock.client.WireMock.get
import com.github.tomakehurst.wiremock.client.WireMock.post
import com.github.tomakehurst.wiremock.client.WireMock.urlPathEqualTo
import com.github.tomakehurst.wiremock.core.WireMockConfiguration
import com.nimbusds.jose.JOSEObjectType
import com.nimbusds.jose.JWSAlgorithm
import com.nimbusds.jose.JWSHeader
import com.nimbusds.jose.crypto.RSASSASigner
import com.nimbusds.jose.jwk.JWKSet
import com.nimbusds.jose.jwk.RSAKey
import com.nimbusds.jose.jwk.gen.RSAKeyGenerator
import com.nimbusds.jwt.JWTClaimsSet
import com.nimbusds.jwt.SignedJWT
import org.springframework.test.context.DynamicPropertyRegistry
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.wait.strategy.Wait
import org.testcontainers.utility.DockerImageName
import java.time.Instant
import java.util.Date

/**
 * Shared, JVM-wide test infrastructure for gateway integration tests:
 *  - one WireMock server standing in for the downstream services, OPA and the IdP's JWKS endpoint;
 *  - one real Redis (quota filter, /health);
 *  - an RSA key whose public half WireMock serves as the JWKS, so tests send real signed JWTs
 *    through the production JWT decoder.
 *
 * Containers start on first use and are removed by Testcontainers' reaper when the JVM exits.
 */
object GatewayTestInfra {

    const val INTERNAL_SECRET = "test-internal-gateway-secret"
    private const val JWKS_PATH = "/oauth/v2/keys"
    const val OPA_ALLOW_PATH = "/v1/data/docintel/authz/allow"

    // Same image as the redis service in docker-compose.yml and document-service's tests.
    val redis: GenericContainer<*> = GenericContainer(DockerImageName.parse("redis:7.4.0-alpine"))
        .withExposedPorts(6379)
        .waitingFor(Wait.forListeningPort())
        .apply { start() }

    val wireMock: WireMockServer = WireMockServer(WireMockConfiguration.options().dynamicPort())
        .apply { start() }

    private val signingKey: RSAKey = RSAKeyGenerator(2048).keyID("test-key").generate()

    /** Same key id as the published key but different key material: signatures must not verify. */
    val foreignKey: RSAKey = RSAKeyGenerator(2048).keyID("test-key").generate()

    val wireMockUrl: String get() = "http://localhost:${wireMock.port()}"

    fun registerProperties(registry: DynamicPropertyRegistry) {
        registry.add("spring.data.redis.host") { redis.host }
        registry.add("spring.data.redis.port") { redis.getMappedPort(6379) }
        registry.add("spring.security.oauth2.resourceserver.jwt.jwk-set-uri") { "$wireMockUrl$JWKS_PATH" }
        registry.add("docintel.opa.url") { wireMockUrl }
        registry.add("INTERNAL_GATEWAY_SECRET") { INTERNAL_SECRET }
        registry.add("DOCUMENT_SERVICE_URL") { wireMockUrl }
        registry.add("RAG_SERVICE_URL") { wireMockUrl }
    }

    /** Clears every stub and request log, then re-adds the JWKS and OPA-allow stubs. */
    fun resetStubs() {
        wireMock.resetAll()
        wireMock.stubFor(
            get(urlPathEqualTo(JWKS_PATH)).willReturn(
                aResponse()
                    .withHeader("Content-Type", "application/json")
                    .withBody(JWKSet(signingKey.toPublicJWK()).toString())
            )
        )
        stubOpa(allow = true)
    }

    fun stubOpa(allow: Boolean) {
        wireMock.stubFor(
            post(urlPathEqualTo(OPA_ALLOW_PATH)).willReturn(
                aResponse()
                    .withHeader("Content-Type", "application/json")
                    .withBody("""{"result": $allow}""")
            )
        )
    }

    /** A JWT signed with the key behind the stubbed JWKS. Omit tenantId to leave the claim out. */
    fun token(
        tenantId: String? = "acme",
        subject: String = "user-1",
        extraClaims: Map<String, Any> = emptyMap(),
        signedBy: RSAKey = signingKey,
    ): String {
        val now = Instant.now()
        val claims = JWTClaimsSet.Builder()
            .subject(subject)
            .issuer("http://idp.test")
            .issueTime(Date.from(now))
            .expirationTime(Date.from(now.plusSeconds(300)))
            .claim("roles", listOf("documents:r", "query:execute"))
            .apply { if (tenantId != null) claim("tenant_id", tenantId) }
            .apply { extraClaims.forEach { (name, value) -> claim(name, value) } }
            .build()
        val header = JWSHeader.Builder(JWSAlgorithm.RS256)
            .keyID(signedBy.keyID)
            .type(JOSEObjectType.JWT)
            .build()
        return SignedJWT(header, claims).apply { sign(RSASSASigner(signedBy)) }.serialize()
    }
}
