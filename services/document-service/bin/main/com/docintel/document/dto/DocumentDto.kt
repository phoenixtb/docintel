package com.docintel.document.dto

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
 * Request to bulk create documents (for sample datasets).
 * Creates document records without file upload.
 */
data class BulkDocumentCreateRequest(
    val tenantId: String,
    val documents: List<BulkDocumentItem>
)

data class BulkDocumentItem(
    val filename: String,
    val domain: String,
    val chunkCount: Int,
    val metadata: Map<String, String> = emptyMap(),
    val content: String? = null  // Optional: raw content for display purposes
)

data class BulkDocumentCreateResponse(
    val created: Int,
    val documentIds: List<UUID>
)
