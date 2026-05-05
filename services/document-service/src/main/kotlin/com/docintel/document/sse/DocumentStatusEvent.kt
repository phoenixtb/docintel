package com.docintel.document.sse

/**
 * Optional shard-level progress payload attached to [DocumentStatusEvent].
 * Published by the ingestion-service after each page shard completes.
 */
data class ProgressPayload(
    val currentPage: Int,
    val totalPages: Int,
    val currentStage: String,   // e.g. "Converting pages 26-50" | "Embedding"
)

/**
 * Internal Spring ApplicationEvent fired at every document lifecycle transition.
 *
 * Published by [DocumentService] (PENDING, PROCESSING) and [IngestionCompleteConsumer]
 * (COMPLETED, FAILED). Also published by [DocumentProgressConsumer] for per-shard progress.
 * Consumed by [SseEmitterRegistry] which broadcasts to connected UI clients for the same tenant.
 *
 * @param stage    Human-readable label shown in the UI ("Queued", "Processing", "Indexed", "Failed")
 * @param progress Optional per-shard progress for large PDFs (null for non-PDF or single-shard)
 */
data class DocumentStatusEvent(
    val documentId: String,
    val tenantId: String,
    val status: String,           // ProcessingStatus enum name
    val stage: String,            // "Queued" | "Processing" | "Indexed" | "Failed"
    val filename: String = "",
    val chunkCount: Int = 0,
    val errorMessage: String? = null,
    val progress: ProgressPayload? = null,
)
