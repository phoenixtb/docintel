package com.docintel.document.repository

import com.docintel.document.entity.Chunk
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.data.jpa.repository.Modifying
import org.springframework.data.jpa.repository.Query
import org.springframework.stereotype.Repository
import org.springframework.transaction.annotation.Transactional
import java.util.UUID

@Repository
interface ChunkRepository : JpaRepository<Chunk, UUID> {
    
    fun findByDocumentId(documentId: UUID): List<Chunk>
    
    fun findByDocumentIdOrderByChunkIndex(documentId: UUID): List<Chunk>
    
    fun countByDocumentId(documentId: UUID): Long

    @Modifying
    @Transactional
    @Query("DELETE FROM Chunk c WHERE c.documentId = :documentId")
    fun deleteByDocumentId(documentId: UUID): Int

    @Modifying
    @Transactional
    @Query("DELETE FROM Chunk c WHERE c.tenantId = :tenantId")
    fun deleteByTenantId(tenantId: String): Int
}
