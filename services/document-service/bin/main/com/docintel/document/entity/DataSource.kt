package com.docintel.document.entity

import jakarta.persistence.*
import org.hibernate.annotations.JdbcTypeCode
import org.hibernate.type.SqlTypes
import java.time.Instant
import java.util.UUID

enum class DataSourceStatus {
    LOADING, COMPLETED, FAILED
}

@Entity
@Table(name = "data_sources")
data class DataSource(
    @Id
    val id: UUID,

    @Column(name = "tenant_id", nullable = false)
    val tenantId: String,

    @Column(name = "source_type", nullable = false)
    val sourceType: String,

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "source_config", columnDefinition = "jsonb")
    val sourceConfig: Map<String, Any?> = emptyMap(),

    @Enumerated(EnumType.STRING)
    @Column(nullable = false)
    var status: DataSourceStatus = DataSourceStatus.LOADING,

    @Column(name = "document_count", nullable = false)
    var documentCount: Int = 0,

    @Column(name = "created_at", nullable = false)
    val createdAt: Instant = Instant.now(),

    @Column(name = "completed_at")
    var completedAt: Instant? = null
)
