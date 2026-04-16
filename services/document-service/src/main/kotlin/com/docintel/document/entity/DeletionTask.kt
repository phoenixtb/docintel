package com.docintel.document.entity

import jakarta.persistence.*
import java.time.Instant
import java.util.UUID

enum class DeletionTaskStatus { PENDING, DONE, DEAD }

/**
 * Outbox record created when a document is marked for deletion.
 *
 * The document row is set to [ProcessingStatus.DELETING] and hidden from user
 * queries atomically with this row being inserted. [DeletionTaskWorker] polls
 * PENDING tasks and drives Qdrant + MinIO cleanup, then removes the document
 * row and marks this task DONE.
 *
 * Failure semantics: each failed attempt increments [attempts] and records
 * [lastAttemptAt] for exponential back-off. After [DeletionTaskWorker.MAX_ATTEMPTS]
 * failures the task moves to DEAD and requires operator attention.
 */
@Entity
@Table(name = "deletion_tasks")
data class DeletionTask(
    @Id
    val id: UUID = UUID.randomUUID(),

    @Column(name = "tenant_id", nullable = false)
    val tenantId: String,

    @Column(name = "document_id", nullable = false)
    val documentId: UUID,

    @Column(name = "file_path", nullable = false)
    val filePath: String,

    @Column(name = "qdrant_done", nullable = false)
    var qdrantDone: Boolean = false,

    @Column(name = "minio_done", nullable = false)
    var minioDone: Boolean = false,

    @Column(name = "attempts", nullable = false)
    var attempts: Int = 0,

    @Column(name = "last_attempt_at")
    var lastAttemptAt: Instant? = null,

    @Enumerated(EnumType.STRING)
    @Column(name = "task_status", nullable = false)
    var taskStatus: DeletionTaskStatus = DeletionTaskStatus.PENDING,

    @Column(name = "created_at", nullable = false)
    val createdAt: Instant = Instant.now(),
)
