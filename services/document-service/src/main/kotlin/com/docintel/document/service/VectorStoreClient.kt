package com.docintel.document.service

import com.docintel.document.filter.HmacUtils
import com.fasterxml.jackson.annotation.JsonProperty
import io.netty.channel.ChannelOption
import io.netty.handler.timeout.ReadTimeoutHandler
import io.netty.handler.timeout.WriteTimeoutHandler
import org.slf4j.MDC
import org.springframework.beans.factory.annotation.Value
import org.springframework.http.client.reactive.ReactorClientHttpConnector
import org.springframework.stereotype.Service
import org.springframework.web.reactive.function.client.WebClient
import org.springframework.web.reactive.function.client.awaitBody
import reactor.netty.http.client.HttpClient
import java.time.Duration
import java.util.UUID
import java.util.concurrent.TimeUnit

data class VectorDeleteResponse(
    val deleted: Boolean,
    @JsonProperty("document_id") val documentId: String? = null,
    @JsonProperty("tenant_id") val tenantId: String? = null
)

/**
 * HTTP client for vector-store (Qdrant) operations in ingestion-service.
 *
 * Only covers deletion endpoints. Document ingestion is now triggered via the
 * [documents.ready] Redis stream rather than a direct REST call.
 */
@Service
class VectorStoreClient(
    @Value("\${ingestion-service.url:http://localhost:8001}")
    private val ingestionServiceUrl: String,
    @Value("\${internal.gateway.secret:}")
    private val internalSecret: String,
) {
    private val httpClient = HttpClient.create()
        .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 5_000)
        .responseTimeout(Duration.ofSeconds(15))
        .doOnConnected { conn ->
            conn.addHandlerLast(ReadTimeoutHandler(15, TimeUnit.SECONDS))
            conn.addHandlerLast(WriteTimeoutHandler(5, TimeUnit.SECONDS))
        }

    private val webClient = WebClient.builder()
        .baseUrl(ingestionServiceUrl)
        .clientConnector(ReactorClientHttpConnector(httpClient))
        .build()

    private fun applyInternalAuth(headers: org.springframework.http.HttpHeaders, tenantId: String) {
        check(internalSecret.isNotBlank()) {
            "INTERNAL_GATEWAY_SECRET not configured — refusing unauthenticated internal call (fail-secure)"
        }
        val token = HmacUtils.compute(":$tenantId:", internalSecret)
        headers.set("X-Tenant-Id", tenantId)
        headers.set("X-Internal-Service-Token", token)
        MDC.get("requestId")?.let { headers.set("X-Request-Id", it) }
    }

    suspend fun deleteDocumentVectors(tenantId: String, documentId: UUID): Boolean {
        return try {
            webClient.delete()
                .uri("/vectors/$tenantId/$documentId")
                .headers { applyInternalAuth(it, tenantId) }
                .retrieve()
                .awaitBody<VectorDeleteResponse>()
            true
        } catch (e: Exception) {
            false
        }
    }

    suspend fun deleteTenantVectors(tenantId: String): Boolean {
        return try {
            webClient.delete()
                .uri("/vectors/$tenantId")
                .headers { applyInternalAuth(it, tenantId) }
                .retrieve()
                .awaitBody<VectorDeleteResponse>()
            true
        } catch (e: Exception) {
            false
        }
    }
}
