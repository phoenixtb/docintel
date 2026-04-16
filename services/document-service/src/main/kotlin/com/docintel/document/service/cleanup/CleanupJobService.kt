package com.docintel.document.service.cleanup

import com.docintel.document.dto.CleanupFiltersRequest
import com.docintel.document.dto.CleanupJobStartResponse
import com.docintel.document.dto.CleanupJobStatus
import com.docintel.document.dto.CleanupJobStatusResponse
import com.docintel.document.service.DocumentService
import com.docintel.document.sse.CleanupSseRegistry
import com.docintel.document.tenant.TenantCoroutineContext
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import java.util.UUID

private const val PROGRESS_EMIT_EVERY = 10

/**
 * Orchestrates async bulk cleanup jobs.
 *
 * [startJob] returns immediately with a job ID (202 pattern). The actual deletion
 * runs in a background coroutine on [Dispatchers.Default].
 *
 * Partial-failure policy: per-document failures are recorded and the job continues.
 * The final SSE event summarises total / succeeded / failed counts and the last
 * [MAX_ERRORS] error messages.
 *
 * Concurrency limit: one active job per tenant (enforced by [CleanupJobRegistry]).
 */
@Service
class CleanupJobService(
    private val documentService: DocumentService,
    private val jobRegistry: CleanupJobRegistry,
    private val sseRegistry: CleanupSseRegistry,
) {
    private val logger = LoggerFactory.getLogger(javaClass)

    private val scope = CoroutineScope(
        SupervisorJob() + Dispatchers.Default +
        CoroutineExceptionHandler { _, t -> logger.error("Unhandled error in cleanup worker", t) }
    )

    /**
     * Create and immediately start an async cleanup job for [tenantId] using [filters].
     *
     * Returns [CleanupJobStartResponse] with the job ID — the caller should subscribe
     * to `GET .../cleanup/jobs/{jobId}/events` for progress.
     *
     * @throws IllegalStateException if another job is already active for this tenant.
     */
    fun startJob(tenantId: String, filters: CleanupFiltersRequest): CleanupJobStartResponse {
        val matchCount = documentService.previewCleanup(tenantId, filters)
        val job = CleanupJob(
            jobId = UUID.randomUUID(),
            tenantId = tenantId,
            filters = filters,
        )

        if (!jobRegistry.tryRegister(job)) {
            throw IllegalStateException(
                "A cleanup job is already active for tenant $tenantId. " +
                "Cancel it or wait for it to complete before starting a new one."
            )
        }

        job.total.set(matchCount)
        logger.info("Cleanup job {} started for tenant={} matchCount={}", job.jobId, tenantId, matchCount)

        scope.launch(TenantCoroutineContext(job.tenantId)) { executeJob(job) }

        return CleanupJobStartResponse(
            jobId = job.jobId,
            tenantId = tenantId,
            matchCount = matchCount,
            status = CleanupJobStatus.QUEUED,
        )
    }

    fun getJobStatus(jobId: UUID, tenantId: String): CleanupJobStatusResponse? =
        jobRegistry.getByJobId(jobId, tenantId)?.toStatusResponse()

    fun cancelJob(jobId: UUID, tenantId: String): Boolean {
        val job = jobRegistry.getByJobId(jobId, tenantId) ?: return false
        if (!job.isActive()) return false
        job.cancelRequested.set(true)
        logger.info("Cancel requested for cleanup job {} (tenant={})", jobId, tenantId)
        return true
    }

    // ─── Worker ──────────────────────────────────────────────────────────────

    private suspend fun executeJob(job: CleanupJob) {
        job.status.set(CleanupJobStatus.RUNNING)
        sseRegistry.sendProgress(job)

        val ids = try {
            documentService.snapshotMatchingIds(job.tenantId, job.filters)
        } catch (e: Exception) {
            logger.error("Cleanup job {} failed during ID snapshot: {}", job.jobId, e.message, e)
            job.status.set(CleanupJobStatus.FAILED)
            job.addError("Snapshot failed: ${e.message}")
            job.completedAt = java.time.Instant.now()
            sseRegistry.sendComplete(job)
            return
        }

        // Update total with actual snapshot size (may differ from preview if docs changed)
        job.total.set(ids.size.toLong())

        for (id in ids) {
            if (job.cancelRequested.get()) break

            try {
                val queued = documentService.markForDeletion(id, job.tenantId)
                if (queued) job.succeeded.incrementAndGet()
                // markForDeletion returns false only if the document no longer exists — not a failure.
            } catch (e: Exception) {
                job.failed.incrementAndGet()
                job.addError("$id: ${e.message}")
                logger.warn("Cleanup job {}: failed to queue document {} for deletion: {}", job.jobId, id, e.message)
            }

            val processed = job.processed.incrementAndGet()
            if (processed % PROGRESS_EMIT_EVERY == 0) {
                sseRegistry.sendProgress(job)
            }
        }

        job.status.set(
            when {
                job.cancelRequested.get() -> CleanupJobStatus.CANCELLED
                job.failed.get() > 0 && job.succeeded.get() == 0 -> CleanupJobStatus.FAILED
                else -> CleanupJobStatus.COMPLETED
            }
        )
        job.completedAt = java.time.Instant.now()

        logger.info(
            "Cleanup job {} finished: status={} succeeded={} failed={} tenant={}",
            job.jobId, job.status.get(), job.succeeded.get(), job.failed.get(), job.tenantId,
        )
        sseRegistry.sendComplete(job)
    }
}
