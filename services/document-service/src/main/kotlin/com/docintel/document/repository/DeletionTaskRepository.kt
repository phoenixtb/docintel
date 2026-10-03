package com.docintel.document.repository

import com.docintel.document.entity.DeletionTask
import com.docintel.document.entity.DeletionTaskStatus
import org.springframework.data.domain.Pageable
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.data.jpa.repository.Modifying
import org.springframework.data.jpa.repository.Query
import org.springframework.data.repository.query.Param
import org.springframework.stereotype.Repository
import java.util.UUID

@Repository
interface DeletionTaskRepository : JpaRepository<DeletionTask, UUID> {

    fun findByTaskStatus(status: DeletionTaskStatus, pageable: Pageable): List<DeletionTask>

    fun countByTaskStatus(status: DeletionTaskStatus): Long

    /**
     * Void queued deletions for a document that is being re-uploaded (content-hash
     * dedupe resurrects the same document id). Atomic UPDATE so a concurrently
     * polling [DeletionTaskWorker] either sees PENDING before this commits or
     * CANCELLED after — never a half-cancelled task.
     */
    @Modifying
    @Query(
        "UPDATE DeletionTask t SET t.taskStatus = com.docintel.document.entity.DeletionTaskStatus.CANCELLED " +
        "WHERE t.documentId = :documentId AND t.tenantId = :tenantId " +
        "AND t.taskStatus = com.docintel.document.entity.DeletionTaskStatus.PENDING"
    )
    fun cancelPendingForDocument(
        @Param("documentId") documentId: UUID,
        @Param("tenantId") tenantId: String,
    ): Int
}
