package com.docintel.document.service.cleanup

import com.docintel.document.dto.CleanupFiltersRequest
import com.docintel.document.dto.CleanupJobStatus
import com.docintel.document.dto.CleanupJobStatusResponse
import java.time.Instant
import java.util.UUID
import java.util.concurrent.ConcurrentLinkedDeque
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

private const val MAX_ERRORS = 20

class CleanupJob(
    val jobId: UUID,
    val tenantId: String,
    val filters: CleanupFiltersRequest,
    val startedAt: Instant = Instant.now(),
) {
    val status: AtomicReference<CleanupJobStatus> = AtomicReference(CleanupJobStatus.QUEUED)
    val total: AtomicLong = AtomicLong(0)
    val processed: AtomicInteger = AtomicInteger(0)
    val succeeded: AtomicInteger = AtomicInteger(0)
    val failed: AtomicInteger = AtomicInteger(0)
    val cancelRequested: AtomicBoolean = AtomicBoolean(false)

    @Volatile var completedAt: Instant? = null

    private val errorDeque = ConcurrentLinkedDeque<String>()

    fun addError(msg: String) {
        errorDeque.addLast(msg)
        while (errorDeque.size > MAX_ERRORS) errorDeque.pollFirst()
    }

    fun errors(): List<String> = errorDeque.toList()

    fun isActive(): Boolean = status.get() in setOf(CleanupJobStatus.QUEUED, CleanupJobStatus.RUNNING)

    fun toStatusResponse() = CleanupJobStatusResponse(
        jobId = jobId,
        tenantId = tenantId,
        status = status.get(),
        total = total.get(),
        processed = processed.get(),
        succeeded = succeeded.get(),
        failed = failed.get(),
        errors = errors(),
        startedAt = startedAt,
        completedAt = completedAt,
    )
}
