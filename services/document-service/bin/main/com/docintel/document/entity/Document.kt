package com.docintel.document.entity

import jakarta.persistence.*
import org.hibernate.annotations.JdbcTypeCode
import org.hibernate.type.SqlTypes
import java.time.Instant
import java.util.UUID

enum class ProcessingStatus {
    PENDING,
    PROCESSING,
    COMPLETED,
    FAILED
}

@Entity
@Table(name = "documents")
data class Document(
    @Id
    val id: UUID,

    @Column(name = "tenant_id", nullable = false)
    val tenantId: String,

    @Column(nullable = false, columnDefinition = "text")
    val filename: String,

    @Column(name = "content_type")
    val contentType: String? = null,

    @Column(name = "file_size")
    val fileSize: Long = 0,

    @Column(name = "file_path", nullable = false, columnDefinition = "text")
    val filePath: String,

    @Enumerated(EnumType.STRING)
    var status: ProcessingStatus = ProcessingStatus.PENDING,

    @Column(name = "chunk_count")
    var chunkCount: Int = 0,

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    val metadata: Map<String, String> = emptyMap(),

    @Column(name = "error_message")
    var errorMessage: String? = null,

    /** Full 64-char sha256 hex of (tenant_id + file_bytes). Stored for observability/dedup queries. */
    @Column(name = "content_hash")
    val contentHash: String? = null,

    /** Null for manual browser uploads; populated for data-loader-originated documents. */
    @Column(name = "data_source_id")
    val dataSourceId: UUID? = null,

    @Column(name = "created_at")
    val createdAt: Instant = Instant.now(),

    @Column(name = "updated_at")
    var updatedAt: Instant = Instant.now()
) {
    @PreUpdate
    fun onUpdate() {
        updatedAt = Instant.now()
    }
}
