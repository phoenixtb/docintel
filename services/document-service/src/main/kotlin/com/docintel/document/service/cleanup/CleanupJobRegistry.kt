package com.docintel.document.service.cleanup

import org.springframework.stereotype.Component
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * In-memory registry of cleanup jobs.
 *
 * Invariant: at most one active (QUEUED or RUNNING) job per tenant at a time.
 * Completed/failed/cancelled jobs remain in [byId] for status queries but do not
 * block a new job from starting.
 *
 * NOTE: In-process only — if document-service is scaled to multiple replicas,
 * migrate job state to Redis or a Postgres table and use pub/sub for SSE fan-out.
 */
@Component
class CleanupJobRegistry {

    private val byTenant = ConcurrentHashMap<String, CleanupJob>()
    private val byId = ConcurrentHashMap<String, CleanupJob>()

    /**
     * Attempt to register [job]. Returns true on success or false if another job
     * is already active for the same tenant.
     */
    fun tryRegister(job: CleanupJob): Boolean {
        val existing = byTenant[job.tenantId]
        if (existing != null && existing.isActive()) return false
        byTenant[job.tenantId] = job
        byId[job.jobId.toString()] = job
        return true
    }

    fun getByJobId(jobId: UUID, tenantId: String): CleanupJob? =
        byId[jobId.toString()]?.takeIf { it.tenantId == tenantId }

    fun getActiveForTenant(tenantId: String): CleanupJob? =
        byTenant[tenantId]?.takeIf { it.isActive() }
}
