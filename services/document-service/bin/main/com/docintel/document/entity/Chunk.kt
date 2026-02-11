package com.docintel.document.entity

import jakarta.persistence.*
import org.hibernate.annotations.JdbcTypeCode
import org.hibernate.type.SqlTypes
import java.time.Instant
import java.util.UUID

@Entity
@Table(name = "chunks")
data class Chunk(
    @Id
    val id: UUID = UUID.randomUUID(),

    @Column(name = "document_id", nullable = false)
    val documentId: UUID,

    @Column(name = "tenant_id", nullable = false)
    val tenantId: String,

    @Column(columnDefinition = "TEXT", nullable = false)
    val content: String,

    @Column(name = "chunk_index", nullable = false)
    val chunkIndex: Int,

    @Column(name = "start_char")
    val startChar: Int = 0,

    @Column(name = "end_char")
    val endChar: Int = 0,

    @Column(name = "token_count")
    val tokenCount: Int = 0,

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    val metadata: Map<String, Any> = emptyMap(),

    @Column(name = "created_at")
    val createdAt: Instant = Instant.now()
)
