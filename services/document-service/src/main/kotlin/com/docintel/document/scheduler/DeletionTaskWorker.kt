package com.docintel.document.scheduler

import com.docintel.document.entity.DeletionTask
import com.docintel.document.entity.DeletionTaskStatus
import com.docintel.document.repository.ChunkRepository
import com.docintel.document.repository.DeletionTaskRepository
import com.docintel.document.repository.DocumentRepository
import com.docintel.document.service.IngestionServiceClient
import com.docintel.document.service.StorageService
import com.docintel.document.tenant.TenantContextHolder
import io.micrometer.core.instrument.MeterRegistry
import kotlinx.coroutines.runBlocking
import org.slf4j.LoggerFactory
import org.springframework.data.domain.PageRequest
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import java.time.Instant
import java.time.temporal.ChronoUnit
import kotlin.math.min
import kotlin.math.pow

/**
 * Polls [DeletionTask] outbox records and drives async cleanup of Qdrant vectors and
 * MinIO files for documents that have been marked [ProcessingStatus.DELETING].
 *
 * Each task tracks per-store completion flags ([DeletionTask.qdrantDone],
 * [DeletionTask.minioDone]) so partial failures are retried cheaply. Once both
 * stores are clean the document and chunk rows are removed from PG and the task is
 * marked [DeletionTaskStatus.DONE].
 *
 * After [MAX_ATTEMPTS] failures the task is moved to [DeletionTaskStatus.DEAD] and
 * surfaced via the `docintel.deletion_tasks.dead` metric gauge.
 */
@Component
class DeletionTaskWorker(
    private val deletionTaskRepository: DeletionTaskRepository,
    private val documentRepository: DocumentRepository,
    private val chunkRepository: ChunkRepository,
    private val ingestionServiceClient: IngestionServiceClient,
    private val storageService: StorageService,
    private val meterRegistry: MeterRegistry,
) {
    private val logger = LoggerFactory.getLogger(javaClass)

    companion object {
        const val BATCH_SIZE = 50
        const val MAX_ATTEMPTS = 10
    }

    @Scheduled(fixedDelay = 30_000L, initialDelay = 15_000L)
    fun process() {
        val tasks = deletionTaskRepository.findByTaskStatus(
            DeletionTaskStatus.PENDING, PageRequest.of(0, BATCH_SIZE)
        )
        if (tasks.isEmpty()) return

        val now = Instant.now()
        logger.debug("DeletionTaskWorker: found {} PENDING tasks", tasks.size)

        for (task in tasks) {
            if (!isReadyForRetry(task, now)) continue
            processTask(task)
        }

        // Update gauges after each batch
        meterRegistry.gauge("docintel.deletion_tasks.pending",
            deletionTaskRepository.countByTaskStatus(DeletionTaskStatus.PENDING).toDouble())
        meterRegistry.gauge("docintel.deletion_tasks.dead",
            deletionTaskRepository.countByTaskStatus(DeletionTaskStatus.DEAD).toDouble())
    }

    private fun isReadyForRetry(task: DeletionTask, now: Instant): Boolean {
        val lastAttempt = task.lastAttemptAt ?: return true
        val backoffMinutes = min(2.0.pow(task.attempts).toLong(), 60L)
        return lastAttempt.isBefore(now.minus(backoffMinutes, ChronoUnit.MINUTES))
    }

    private fun processTask(task: DeletionTask) {
        try {
            TenantContextHolder.setTenantId(task.tenantId)
            TenantContextHolder.setUserRole("platform_admin")

            if (!task.qdrantDone) {
                val deleted = runBlocking {
                    ingestionServiceClient.deleteDocumentVectors(task.tenantId, task.documentId)
                }
                if (deleted) {
                    task.qdrantDone = true
                } else {
                    logger.warn("Task {}: Qdrant delete returned false for document {} — will retry", task.id, task.documentId)
                }
            }

            if (!task.minioDone) {
                try {
                    storageService.deleteDocumentFiles(task.tenantId, task.filePath)
                    task.minioDone = true
                } catch (e: Exception) {
                    logger.warn("Task {}: MinIO delete failed for document {}: {}", task.id, task.documentId, e.message)
                }
            }

            if (task.qdrantDone && task.minioDone) {
                completeTask(task)
            } else {
                recordAttempt(task)
            }
        } catch (e: Exception) {
            logger.error("DeletionTaskWorker: unexpected error for task {}: {}", task.id, e.message, e)
            recordAttempt(task)
        } finally {
            TenantContextHolder.clear()
        }
    }

    /**
     * Both stores are clean: remove chunks + document from PG, mark task DONE.
     * Each repository call has its own [Transactional]; all are idempotent —
     * if the worker crashes after chunk delete the next run safely re-runs the
     * already-no-op delete and proceeds to mark DONE.
     */
    private fun completeTask(task: DeletionTask) {
        chunkRepository.deleteByDocumentId(task.documentId)
        documentRepository.deleteByIdAndTenantId(task.documentId, task.tenantId)
        task.taskStatus = DeletionTaskStatus.DONE
        deletionTaskRepository.save(task)
        logger.info("DeletionTaskWorker: document {} (tenant={}) fully deleted", task.documentId, task.tenantId)
    }

    private fun recordAttempt(task: DeletionTask) {
        task.attempts++
        task.lastAttemptAt = Instant.now()
        if (task.attempts >= MAX_ATTEMPTS) {
            task.taskStatus = DeletionTaskStatus.DEAD
            logger.error(
                "DeletionTaskWorker: task {} DEAD after {} attempts — document={} tenant={} qdrantDone={} minioDone={}",
                task.id, task.attempts, task.documentId, task.tenantId, task.qdrantDone, task.minioDone
            )
        }
        deletionTaskRepository.save(task)
    }
}
