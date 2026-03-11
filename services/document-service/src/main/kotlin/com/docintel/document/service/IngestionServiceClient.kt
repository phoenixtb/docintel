package com.docintel.document.service

import com.fasterxml.jackson.annotation.JsonProperty
import io.netty.channel.ChannelOption
import io.netty.handler.timeout.ReadTimeoutHandler
import io.netty.handler.timeout.WriteTimeoutHandler
import org.springframework.beans.factory.annotation.Value
import org.springframework.http.MediaType
import org.springframework.http.client.reactive.ReactorClientHttpConnector
import org.springframework.stereotype.Service
import org.springframework.web.reactive.function.client.WebClient
import org.springframework.web.reactive.function.client.awaitBody
import reactor.netty.http.client.HttpClient
import java.time.Duration
import java.util.UUID
import java.util.concurrent.TimeUnit

data class IngestionTriggerRequest(
    @JsonProperty("document_id") val documentId: String,
    @JsonProperty("tenant_id") val tenantId: String,
    val bucket: String,
    @JsonProperty("object_path") val objectPath: String,
    val filename: String,
    @JsonProperty("domain_hint") val domainHint: String = "auto",
    val metadata: Map<String, Any> = emptyMap()
)

data class IngestionTriggerResponse(
    val status: String,
    @JsonProperty("document_id") val documentId: String
)

data class VectorDeleteResponse(
    val deleted: Boolean,
    @JsonProperty("document_id") val documentId: String? = null,
    @JsonProperty("tenant_id") val tenantId: String? = null
)

@Service
class IngestionServiceClient(
    @Value("\${ingestion-service.url:http://localhost:8001}")
    private val ingestionServiceUrl: String
) {
    private val httpClient = HttpClient.create()
        .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 5_000)
        .responseTimeout(Duration.ofSeconds(10))  // trigger is fast; ingestion runs async
        .doOnConnected { conn ->
            conn.addHandlerLast(ReadTimeoutHandler(10, TimeUnit.SECONDS))
            conn.addHandlerLast(WriteTimeoutHandler(5, TimeUnit.SECONDS))
        }

    private val webClient = WebClient.builder()
        .baseUrl(ingestionServiceUrl)
        .clientConnector(ReactorClientHttpConnector(httpClient))
        .build()

    /**
     * Trigger async ingestion for an uploaded document.
     * Returns immediately once ingestion-service accepts the job.
     * The ingestion-service updates document status and chunks in PG on completion.
     */
    suspend fun triggerIngestion(
        documentId: UUID,
        tenantId: String,
        bucket: String,
        objectPath: String,
        filename: String,
        domainHint: String = "auto",
        metadata: Map<String, Any> = emptyMap()
    ): IngestionTriggerResponse {
        return webClient.post()
            .uri("/ingest")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(
                IngestionTriggerRequest(
                    documentId = documentId.toString(),
                    tenantId = tenantId,
                    bucket = bucket,
                    objectPath = objectPath,
                    filename = filename,
                    domainHint = if (domainHint.isBlank()) "auto" else domainHint,
                    metadata = metadata
                )
            )
            .retrieve()
            .awaitBody()
    }

    /**
     * Delete all vectors for a specific document from Qdrant.
     */
    suspend fun deleteDocumentVectors(tenantId: String, documentId: UUID): Boolean {
        return try {
            webClient.delete()
                .uri("/vectors/$tenantId/$documentId")
                .retrieve()
                .awaitBody<VectorDeleteResponse>()
            true
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Delete all vectors for an entire tenant (used when deleting the tenant).
     */
    suspend fun deleteTenantVectors(tenantId: String): Boolean {
        return try {
            webClient.delete()
                .uri("/vectors/$tenantId")
                .retrieve()
                .awaitBody<VectorDeleteResponse>()
            true
        } catch (e: Exception) {
            false
        }
    }
}
