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

    /** Find COMPLETED documents by IDs; used by reconciliation sweeper for reverse-orphan check. */
    @Query("SELECT d FROM Document d WHERE d.id IN :ids AND d.status = :status")
    fun findByIdsAndStatus(ids: List<UUID>, status: ProcessingStatus): List<Document>
}
