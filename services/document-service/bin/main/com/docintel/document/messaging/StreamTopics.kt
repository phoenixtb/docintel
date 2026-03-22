package com.docintel.document.messaging

/**
 * Centralised stream topic names and event payload data classes shared by all
 * producers/consumers within document-service.
 *
 * Keep in sync with docintel_common.messaging (Python) TOPIC_* constants.
 */
object StreamTopics {
    const val FILES_AVAILABLE   = "files.available"
    const val DOCUMENTS_READY   = "documents.ready"
    const val INGESTION_COMPLETE = "ingestion.complete"
}

/** Published by data-loader when a file is uploaded to MinIO and ready for registration. */
data class FilesAvailableEvent(
    val minioPath: String,
    val contentHash: String,
    val tenantId: String,
    val filename: String,
    val contentType: String = "application/octet-stream",
    val fileSize: Long = 0L,
    val dataSourceId: String? = null,
    val domainHint: String = "auto",
    val metadata: Map<String, String> = emptyMap()
)

/** Published by document-service after a document record is persisted; consumed by ingestion-service. */
data class DocumentReadyEvent(
    val documentId: String,
    val tenantId: String,
    val bucket: String,
    val objectPath: String,
    val filename: String,
    val domainHint: String = "auto",
    val metadata: Map<String, String> = emptyMap()
)

/** Published by ingestion-service on completion; consumed by document-service to update status. */
data class IngestionCompleteEvent(
    val documentId: String,
    val tenantId: String,
    val chunkCount: Int,
    val domain: String,
    val status: String,            // "COMPLETED" | "FAILED"
    val errorMessage: String? = null
)
