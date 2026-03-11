package com.docintel.document.repository

import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import org.springframework.data.domain.Page
import org.springframework.data.domain.Pageable
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.data.jpa.repository.Query
import org.springframework.stereotype.Repository
import java.time.Instant
import java.util.UUID

@Repository
interface DocumentRepository : JpaRepository<Document, UUID> {
    
    fun findByTenantId(tenantId: String, pageable: Pageable): Page<Document>
    
    fun findByTenantId(tenantId: String): List<Document>
    
    fun findByTenantIdAndStatus(
        tenantId: String, 
        status: ProcessingStatus, 
        pageable: Pageable
    ): Page<Document>
    
    fun findByIdAndTenantId(id: UUID, tenantId: String): Document?
    
    fun countByTenantId(tenantId: String): Long
    
    fun deleteByIdAndTenantId(id: UUID, tenantId: String): Long

    /** Find stale PENDING/PROCESSING documents for the retry sweeper. */
    @Query("SELECT d FROM Document d WHERE d.status IN :statuses AND d.createdAt < :before")
    fun findStaleByStatusIn(
        statuses: List<ProcessingStatus>,
        before: Instant,
        pageable: Pageable,
    ): List<Document>
}
