package com.docintel.document.repository

import com.docintel.document.entity.Chunk
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.stereotype.Repository
import java.util.UUID

@Repository
interface ChunkRepository : JpaRepository<Chunk, UUID> {
    
    fun findByDocumentId(documentId: UUID): List<Chunk>
    
    fun findByDocumentIdOrderByChunkIndex(documentId: UUID): List<Chunk>
    
    fun countByDocumentId(documentId: UUID): Long
    
    fun deleteByDocumentId(documentId: UUID): Long
    
    fun deleteByTenantId(tenantId: String): Long
}
