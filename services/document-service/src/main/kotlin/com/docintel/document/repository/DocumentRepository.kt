package com.docintel.document.repository

import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import org.springframework.data.domain.Page
import org.springframework.data.domain.Pageable
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.data.jpa.repository.JpaSpecificationExecutor
import org.springframework.data.jpa.repository.Modifying
import org.springframework.data.jpa.repository.Query
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import java.time.Instant
import java.util.UUID

@Repository
interface DocumentRepository : JpaRepository<Document, UUID>, JpaSpecificationExecutor<Document> {

    /** All non-DELETING documents for a tenant. Used for user-facing list endpoint. */
    fun findByTenantIdAndStatusNot(tenantId: String, excludedStatus: ProcessingStatus, pageable: Pageable): Page<Document>

    fun findByTenantIdAndStatusNot(tenantId: String, excludedStatus: ProcessingStatus): List<Document>

    fun findByTenantId(tenantId: String, pageable: Pageable): Page<Document>

    fun findByTenantId(tenantId: String): List<Document>

    fun findByTenantIdAndStatus(
        tenantId: String,
        status: ProcessingStatus,
        pageable: Pageable
    ): Page<Document>

    fun findByIdAndTenantId(id: UUID, tenantId: String): Document?

    fun countByTenantId(tenantId: String): Long

    @Modifying
    @Transactional
    @Query("DELETE FROM Document d WHERE d.id = :id AND d.tenantId = :tenantId")
    fun deleteByIdAndTenantId(id: UUID, tenantId: String): Int

    /** Dedup check: find an existing document by its content hash within a tenant. */
    fun findByContentHashAndTenantId(contentHash: String, tenantId: String): Document?

    /** Find all in-flight documents for a tenant (SSE snapshot on connect). */
    fun findByTenantIdAndStatusIn(tenantId: String, statuses: List<ProcessingStatus>): List<Document>

    /** Find stale documents (not in a terminal or DELETING state) using updatedAt for correctness. */
    @Query("SELECT d FROM Document d WHERE d.status IN :statuses AND d.updatedAt < :before")
    fun findStaleByStatusIn(
        statuses: List<ProcessingStatus>,
        before: Instant,
        pageable: Pageable,
    ): List<Document>

    /** All document IDs for a tenant excluding DELETING; used by reconciliation sweeper. */
    @Query("SELECT d.id FROM Document d WHERE d.tenantId = :tenantId AND d.status <> :excludedStatus")
    fun findIdsByTenantIdAndStatusNot(tenantId: String, excludedStatus: ProcessingStatus): List<UUID>

    /** Distinct tenant IDs across all documents; used by reconciliation sweeper. */
    @Query("SELECT DISTINCT d.tenantId FROM Document d WHERE d.status <> 'DELETING'")
    fun findDistinctActiveTenantIds(): List<String>

    /** Stats: count by status for a tenant (excluding DELETING). */
    @Query("SELECT d.status, COUNT(d) FROM Document d WHERE d.tenantId = :tenantId AND d.status <> com.docintel.document.entity.ProcessingStatus.DELETING GROUP BY d.status")
    fun countByStatusForTenant(tenantId: String): List<Array<Any>>

    @Query(
        value = "SELECT COALESCE(metadata->>'domain', 'unknown') AS domain, COUNT(*) FROM documents WHERE tenant_id = :tenantId AND status <> 'DELETING' GROUP BY domain",
        nativeQuery = true
    )
    fun countByDomainForTenant(tenantId: String): List<Array<Any>>

    @Query(
        value = "SELECT CASE WHEN metadata->>'source' = 'sample_dataset' THEN 'sample' ELSE 'manual' END AS src, COUNT(*) FROM documents WHERE tenant_id = :tenantId AND status <> 'DELETING' GROUP BY src",
        nativeQuery = true
    )
    fun countBySourceForTenant(tenantId: String): List<Array<Any>>

    @Query(
        value = "SELECT COALESCE(SUM(file_size), 0) FROM documents WHERE tenant_id = :tenantId AND status <> 'DELETING'",
        nativeQuery = true
    )
    fun sumFileSizeForTenant(tenantId: String): Long

    @Query(
        value = "SELECT COALESCE(SUM(chunk_count), 0) FROM documents WHERE tenant_id = :tenantId AND status <> 'DELETING'",
        nativeQuery = true
    )
    fun sumChunkCountForTenant(tenantId: String): Long

    @Query(
        value = "SELECT MAX(created_at) FROM documents WHERE tenant_id = :tenantId AND status <> 'DELETING'",
        nativeQuery = true
    )
    fun findLatestCreatedAtForTenant(tenantId: String): java.sql.Timestamp?

    /** Find COMPLETED documents by IDs; used by reconciliation sweeper for reverse-orphan check. */
    @Query("SELECT d FROM Document d WHERE d.id IN :ids AND d.status = :status")
    fun findByIdsAndStatus(ids: List<UUID>, status: ProcessingStatus): List<Document>
}
