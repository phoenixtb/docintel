package com.docintel.document.scheduler

import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.repository.DocumentRepository
import com.docintel.document.service.QdrantRestClient
import com.docintel.document.tenant.TenantContextHolder
import io.micrometer.core.instrument.MeterRegistry
import org.slf4j.LoggerFactory
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import org.springframework.transaction.annotation.Transactional
import java.time.Duration
import java.time.Instant
import java.util.UUID

/**
 * Daily cross-store reconciliation job that detects and eliminates drift between
 * PostgreSQL (source of truth) and Qdrant (derived vector store).
 *
 * ## Step 1 — Qdrant orphan removal
 * Scrolls every point in `documents_{tenant}` and collects the unique
 * `meta.document_id` values. Any ID that has no matching ACTIVE PG document is
 * an orphan (document was deleted without cleaning up Qdrant). Those points are
 * bulk-deleted from Qdrant.
 *
 * ## Step 2 — PG reverse-orphan detection (logged, not auto-fixed)
 * Finds COMPLETED PG documents whose ID does not appear in Qdrant. These are
 * logged for operator awareness; auto re-queue can be enabled via
 * `reconciliation.auto-requeue=true` (default false) once the root cause
 * of vector loss is understood.
 *
 * Runs daily at 02:00 by default (configurable via `reconciliation.cron`).
 */
@Component
class ReconciliationSweeper(
    private val documentRepository: DocumentRepository,
    private val qdrantClient: QdrantRestClient,
    private val meterRegistry: MeterRegistry,
) {
    private val logger = LoggerFactory.getLogger(javaClass)

    @Volatile private var lastRunAt: Instant? = null

    @Scheduled(cron = "\${reconciliation.cron:0 0 2 * * *}")
    fun reconcile() {
        val start = Instant.now()
        logger.info("ReconciliationSweeper: starting")

        val tenants = documentRepository.findDistinctActiveTenantIds()
        var totalOrphansDeleted = 0
        var totalReverseOrphans = 0

        for (tenantId in tenants) {
            try {
                TenantContextHolder.setTenantId(tenantId)
                TenantContextHolder.setUserRole("platform_admin")
                val (orphans, reverseOrphans) = reconcileTenant(tenantId)
                totalOrphansDeleted += orphans
                totalReverseOrphans += reverseOrphans
            } catch (e: Exception) {
                logger.error("ReconciliationSweeper: failed for tenant {}: {}", tenantId, e.message, e)
            } finally {
                TenantContextHolder.clear()
            }
        }

        lastRunAt = Instant.now()
        val elapsed = Duration.between(start, lastRunAt).toMillis()
        logger.info(
            "ReconciliationSweeper: done in {}ms — tenants={} qdrantOrphansDeleted={} pgReverseOrphans={}",
            elapsed, tenants.size, totalOrphansDeleted, totalReverseOrphans,
        )

        meterRegistry.counter("docintel.reconciliation.qdrant_orphans_deleted")
            .increment(totalOrphansDeleted.toDouble())
        meterRegistry.gauge("docintel.reconciliation.last_run_age_seconds",
            Duration.between(start, lastRunAt).toSeconds().toDouble())
    }

    private fun reconcileTenant(tenantId: String): Pair<Int, Int> {
        if (!qdrantClient.collectionExists(tenantId)) {
            logger.debug("ReconciliationSweeper: no Qdrant collection for tenant {}, skipping", tenantId)
            return Pair(0, 0)
        }

        // All Qdrant document IDs for this tenant
        val qdrantIds: Set<UUID> = qdrantClient.scrollDocumentIds(tenantId)
        if (qdrantIds.isEmpty()) return Pair(0, 0)

        // Active PG document IDs (excludes DELETING — those are already queued for cleanup)
        val pgIds: Set<UUID> = documentRepository
            .findIdsByTenantIdAndStatusNot(tenantId, ProcessingStatus.DELETING)
            .toHashSet()

        // Step 1: orphans in Qdrant with no PG document
        val orphanedInQdrant = (qdrantIds - pgIds).toList()
        val deleted = if (orphanedInQdrant.isNotEmpty()) {
            logger.info(
                "ReconciliationSweeper: tenant={} — {} Qdrant orphans found, deleting",
                tenantId, orphanedInQdrant.size,
            )
            qdrantClient.deleteOrphanedPoints(tenantId, orphanedInQdrant).also {
                logger.info("ReconciliationSweeper: tenant={} — {} orphan points deleted", tenantId, it)
            }
        } else 0

        // Step 2: PG COMPLETED docs that have no vectors in Qdrant (reverse orphans — log only)
        val pgIdsWithVectors = pgIds.intersect(qdrantIds)
        val reverseOrphanIds = (pgIds - pgIdsWithVectors).toList()
        val reverseOrphans = if (reverseOrphanIds.isNotEmpty()) {
            val completedWithoutVectors = findCompletedWithoutVectors(reverseOrphanIds)
            if (completedWithoutVectors.isNotEmpty()) {
                logger.warn(
                    "ReconciliationSweeper: tenant={} — {} COMPLETED documents have no Qdrant vectors (IDs: {}). " +
                    "Consider manual reprocess or enable reconciliation.auto-requeue.",
                    tenantId, completedWithoutVectors.size,
                    completedWithoutVectors.take(5).joinToString(),
                )
                meterRegistry.counter("docintel.reconciliation.pg_reverse_orphans")
                    .increment(completedWithoutVectors.size.toDouble())
            }
            completedWithoutVectors.size
        } else 0

        return Pair(deleted, reverseOrphans)
    }

    @Transactional(readOnly = true)
    fun findCompletedWithoutVectors(candidateIds: List<UUID>): List<UUID> {
        if (candidateIds.isEmpty()) return emptyList()
        return candidateIds.chunked(500).flatMap { batch ->
            documentRepository.findByIdsAndStatus(batch, ProcessingStatus.COMPLETED).map { it.id }
        }
    }
}
