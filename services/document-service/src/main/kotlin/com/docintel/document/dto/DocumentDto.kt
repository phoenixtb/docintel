package com.docintel.document.dto

import com.docintel.document.entity.DataSourceStatus
import com.docintel.document.entity.ProcessingStatus
import java.time.Instant
import java.util.UUID

data class DocumentResponse(
    val id: UUID,
    val filename: String,
    val contentType: String?,
    val fileSize: Long,
    val chunkCount: Int,
    val status: ProcessingStatus,
    val metadata: Map<String, String>,
    val createdAt: Instant,
    val updatedAt: Instant
)

data class DocumentDetailResponse(
    val id: UUID,
    val filename: String,
    val contentType: String?,
    val fileSize: Long,
    val chunkCount: Int,
    val status: ProcessingStatus,
    val metadata: Map<String, String>,
    val errorMessage: String?,
    val chunks: List<ChunkResponse>?,
    val createdAt: Instant,
    val updatedAt: Instant
)

data class ChunkResponse(
    val id: UUID,
    val chunkIndex: Int,
    val content: String,
    val startChar: Int,
    val endChar: Int,
    val tokenCount: Int,
    val metadata: Map<String, Any>
)

data class DocumentUploadRequest(
    val tenantId: String = "default",
    val metadata: Map<String, String> = emptyMap()
)

data class ProcessingResult(
    val documentId: UUID,
    val chunkCount: Int,
    val status: ProcessingStatus,
    val errorMessage: String? = null
)

/**
 * Request to register a document that already exists in MinIO.
 * Used by data-loader after it has uploaded file bytes to the content-addressable path.
 *
 * Path convention: {tenant_id}/docs/{content_hash}/original.{ext}
 */
data class FromPathRequest(
    val minioPath: String,
    val contentHash: String,
    val filename: String,
    val fileSize: Long = 0,
    val contentType: String? = null,
    val dataSourceId: UUID? = null,
    val metadata: Map<String, String> = emptyMap(),
    val domainHint: String = "auto"
)

/**
 * Response for dedup scenarios: wraps the existing or newly created document
 * plus a flag indicating whether it was a dedup hit.
 */
data class FromPathResponse(
    val document: DocumentResponse,
    val deduplicated: Boolean
)

/**
 * Single chunk record sent by ingestion-service via the bulk persist API.
 * Replaces the direct psycopg2 INSERT that ingestion-service used to perform.
 */
data class ChunkPersistRequest(
    val chunkId: UUID,
    val chunkIndex: Int,
    val content: String,
    val startChar: Int = 0,
    val endChar: Int = 0,
    val tokenCount: Int = 0,
    val metadata: Map<String, Any> = emptyMap()
)

data class DocumentStatsResponse(
    val totalDocuments: Long,
    val totalChunks: Long,
    val totalBytes: Long,
    val byStatus: Map<String, Long>,
    val byDomain: Map<String, Long>,
    val bySource: Map<String, Long>,
    val lastUploadedAt: Instant?,
)

data class DataSourceRequest(
    val sourceType: String,
    val sourceConfig: Map<String, Any?> = emptyMap()
)

data class DataSourceResponse(
    val id: UUID,
    val tenantId: String,
    val sourceType: String,
    val sourceConfig: Map<String, Any?>,
    val status: DataSourceStatus,
    val documentCount: Int,
    val createdAt: Instant,
    val completedAt: Instant?
)
