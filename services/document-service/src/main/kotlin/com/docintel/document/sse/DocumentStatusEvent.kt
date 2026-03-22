package com.docintel.document.sse

/**
 * Internal Spring ApplicationEvent fired at every document lifecycle transition.
 *
 * Published by [DocumentService] (PENDING, PROCESSING) and [IngestionCompleteConsumer]
 * (COMPLETED, FAILED). Consumed by [SseEmitterRegistry] which broadcasts to connected
 * UI clients for the same tenant.
 *
 * @param stage  Human-readable label shown in the UI ("Queued", "Processing", "Indexed", "Failed")
 */
data class DocumentStatusEvent(
    val documentId: String,
    val tenantId: String,
    val status: String,      // ProcessingStatus enum name
    val stage: String,       // "Queued" | "Processing" | "Indexed" | "Failed"
    val filename: String = "",
    val chunkCount: Int = 0,
    val errorMessage: String? = null,
)
