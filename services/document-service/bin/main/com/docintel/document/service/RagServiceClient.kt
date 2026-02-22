package com.docintel.document.service

import com.fasterxml.jackson.annotation.JsonProperty
import org.springframework.beans.factory.annotation.Value
import org.springframework.http.MediaType
import org.springframework.stereotype.Service
import org.springframework.web.reactive.function.client.WebClient
import org.springframework.web.reactive.function.client.awaitBody
import java.util.UUID

data class ChunkData(
    @JsonProperty("chunk_id") val chunkId: String,
    val content: String,
    val metadata: Map<String, Any> = emptyMap()
)

data class ChunkRequest(
    val text: String,
    @JsonProperty("document_id") val documentId: String,
    @JsonProperty("tenant_id") val tenantId: String,
    val filename: String,
    val method: String = "recursive",
    @JsonProperty("chunk_size") val chunkSize: Int = 400,
    @JsonProperty("chunk_overlap") val chunkOverlap: Int = 0,
    val metadata: Map<String, Any> = emptyMap()
)

data class ChunkResponseItem(
    @JsonProperty("chunk_id") val chunkId: String,
    val content: String,
    @JsonProperty("start_char") val startChar: Int,
    @JsonProperty("end_char") val endChar: Int,
    @JsonProperty("token_count") val tokenCount: Int,
    val metadata: Map<String, Any> = emptyMap()
)

data class RagChunkResponse(
    @JsonProperty("document_id") val documentId: String,
    @JsonProperty("chunk_count") val chunkCount: Int,
    val chunks: List<ChunkResponseItem>
)

data class IndexRequest(
    @JsonProperty("document_id") val documentId: String,
    @JsonProperty("tenant_id") val tenantId: String,
    val chunks: List<ChunkData>
)

data class IndexResponse(
    val status: String,
    @JsonProperty("document_id") val documentId: String,
    @JsonProperty("embedded_count") val embeddedCount: Int,
    val collection: String
)

data class ClassifyDomainRequest(
    val content: String
)

data class ClassifyDomainResponse(
    val domain: String,
    val confidence: Double,
    @JsonProperty("all_scores") val allScores: Map<String, Double>
)

@Service
class RagServiceClient(
    @Value("\${rag-service.url:http://localhost:8000}")
    private val ragServiceUrl: String
) {
    private val webClient = WebClient.builder()
        .baseUrl(ragServiceUrl)
        .build()

    /**
     * Send text to RAG service for chunking.
     */
    suspend fun chunkText(
        text: String,
        documentId: UUID,
        tenantId: String,
        filename: String,
        metadata: Map<String, Any> = emptyMap()
    ): RagChunkResponse {
        return webClient.post()
            .uri("/chunk")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(
                ChunkRequest(
                    text = text,
                    documentId = documentId.toString(),
                    tenantId = tenantId,
                    filename = filename,
                    metadata = metadata
                )
            )
            .retrieve()
            .awaitBody()
    }

    /**
     * Send chunks to RAG service for embedding and indexing.
     */
    suspend fun indexChunks(
        chunks: List<ChunkData>,
        documentId: UUID,
        tenantId: String
    ): IndexResponse {
        return webClient.post()
            .uri("/index")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(
                IndexRequest(
                    documentId = documentId.toString(),
                    tenantId = tenantId,
                    chunks = chunks
                )
            )
            .retrieve()
            .awaitBody()
    }

    /**
     * Delete vectors for a document.
     */
    suspend fun deleteDocumentVectors(tenantId: String, documentId: UUID): Boolean {
        return try {
            webClient.delete()
                .uri("/index/$tenantId/$documentId")
                .retrieve()
                .awaitBody<Map<String, Any>>()
            true
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Delete all vectors for a tenant.
     */
    suspend fun deleteTenantVectors(tenantId: String): Boolean {
        return try {
            webClient.delete()
                .uri("/index/$tenantId")
                .retrieve()
                .awaitBody<Map<String, Any>>()
            true
        } catch (e: Exception) {
            false
        }
    }

    /**
     * Classify text into a domain using zero-shot classification.
     */
    suspend fun classifyDomain(content: String): ClassifyDomainResponse {
        return webClient.post()
            .uri("/classify-domain")
            .contentType(MediaType.APPLICATION_JSON)
            .bodyValue(ClassifyDomainRequest(content = content.take(5000)))
            .retrieve()
            .awaitBody()
    }
}
