package com.docintel.document.dto

import com.docintel.document.entity.ProcessingStatus
import java.time.Instant
import java.util.UUID

enum class UploadOrigin { MANUAL, DATA_SOURCE }

enum class CleanupJobStatus {
    QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED;
    fun isTerminal() = this == COMPLETED || this == FAILED || this == CANCELLED
}

/**
 * Filter criteria for cleanup preview and bulk delete.
 * All fields are optional; active fields are combined with AND semantics.
 *
 * [targetTenantId] — platform admin only. If set to a different tenant than
 * the caller's own, the controller validates documents:delete_all in X-User-Roles.
 */
data class CleanupFiltersRequest(
    val statuses: List<ProcessingStatus>? = null,
    val createdAfter: Instant? = null,
    val createdBefore: Instant? = null,
    /** Matches metadata['domain'] */
    val domain: String? = null,
    /** Exact match or prefix — e.g. "application/pdf" or "image/" for all image types */
    val contentType: String? = null,
    /** MANUAL = data_source_id IS NULL; DATA_SOURCE = IS NOT NULL */
    val uploadOrigin: UploadOrigin? = null,
    /** Specific data source — overrides uploadOrigin if set */
    val dataSourceId: UUID? = null,
    /** Matches metadata['source'] (e.g. "sample_dataset") */
    val metadataSource: String? = null,
    /** Platform admin cross-tenant target. Null = use caller's own tenant. */
    val targetTenantId: String? = null,
)

data class CleanupPreviewResponse(
    val matchCount: Long,
    val tenantId: String,
)

data class CleanupJobStartResponse(
    val jobId: UUID,
    val tenantId: String,
    val matchCount: Long,
    val status: CleanupJobStatus,
)

data class CleanupJobStatusResponse(
    val jobId: UUID,
    val tenantId: String,
    val status: CleanupJobStatus,
    val total: Long,
    val processed: Int,
    val succeeded: Int,
    val failed: Int,
    val errors: List<String>,
    val startedAt: Instant,
    val completedAt: Instant?,
)
