package com.docintel.document.service

import com.docintel.document.dto.*
import com.docintel.document.entity.Chunk
import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.repository.ChunkRepository
import com.docintel.document.repository.DocumentRepository
import org.slf4j.LoggerFactory
import org.springframework.data.domain.Page
import org.springframework.data.domain.PageRequest
import org.springframework.data.domain.Pageable
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import org.springframework.web.multipart.MultipartFile
import java.util.UUID

@Service
class DocumentService(
    private val documentRepository: DocumentRepository,
    private val chunkRepository: ChunkRepository,
    private val storageService: StorageService,
    private val ingestionServiceClient: IngestionServiceClient
) {
    private val logger = LoggerFactory.getLogger(DocumentService::class.java)

    /**
     * Upload and store a document, then trigger async processing.
     */
    @Transactional
    fun uploadDocument(
        file: MultipartFile,
        tenantId: String,
        metadata: Map<String, String> = emptyMap()
    ): DocumentResponse {
        val documentId = UUID.randomUUID()
        
        val filePath = storageService.storeFile(file, tenantId, documentId)
        
        val document = Document(
            id = documentId,
            tenantId = tenantId,
            filename = file.originalFilename ?: "unknown",
            contentType = file.contentType,
            fileSize = file.size,
            filePath = filePath,
            status = ProcessingStatus.PENDING,
            metadata = metadata
        )
        
        val saved = documentRepository.save(document)
        
        return saved.toResponse()
    }

    /**
     * Trigger ingestion for a document.
     *
     * NOT annotated @Transactional because the function suspends inside triggerIngestion
     * (WebClient HTTP call), which would cause the Spring ThreadLocal transaction context
     * to be lost when the coroutine resumes on a different thread. Instead, DB writes are
     * performed in small, non-suspend @Transactional helper methods.
     *
     * @param domainHint: "auto" for zero-shot classification, or a specific domain name.
     */
    suspend fun processDocument(
        documentId: UUID,
        tenantId: String,
        domainHint: String = "auto"
    ): ProcessingResult {
        val doc = fetchDocumentForIngestion(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found: $documentId")

        markDocumentProcessing(documentId, tenantId)

        return try {
            ingestionServiceClient.triggerIngestion(
                documentId = documentId,
                tenantId = tenantId,
                bucket = "docintel-$tenantId",
                objectPath = doc.filePath,
                filename = doc.filename,
                domainHint = if (domainHint.isBlank()) "auto" else domainHint,
                metadata = doc.metadata.toMap()
            )
            logger.info(
                "Ingestion triggered for document {} (tenant={}, file={})",
                documentId, tenantId, doc.filename
            )
            ProcessingResult(documentId = documentId, chunkCount = 0, status = ProcessingStatus.PROCESSING)

        } catch (e: Exception) {
            logger.error("Failed to trigger ingestion for document {}: {}", documentId, e.message, e)
            markDocumentFailed(documentId, tenantId, e.message)
            ProcessingResult(documentId = documentId, chunkCount = 0, status = ProcessingStatus.FAILED, errorMessage = e.message)
        }
    }

    @Transactional(readOnly = true)
    fun fetchDocumentForIngestion(documentId: UUID, tenantId: String): Document? =
        documentRepository.findByIdAndTenantId(documentId, tenantId)

    @Transactional
    fun markDocumentProcessing(documentId: UUID, tenantId: String) {
        val doc = documentRepository.findByIdAndTenantId(documentId, tenantId) ?: return
        doc.status = ProcessingStatus.PROCESSING
        documentRepository.save(doc)
    }

    @Transactional
    fun markDocumentFailed(documentId: UUID, tenantId: String, message: String?) {
        val doc = documentRepository.findByIdAndTenantId(documentId, tenantId) ?: return
        doc.status = ProcessingStatus.FAILED
        doc.errorMessage = message
        documentRepository.save(doc)
    }

    /**
     * Get document by ID.
     */
    fun getDocument(id: UUID, tenantId: String, includeChunks: Boolean = false): DocumentDetailResponse? {
        val document = documentRepository.findByIdAndTenantId(id, tenantId) ?: return null
        
        val chunks = if (includeChunks) {
            chunkRepository.findByDocumentIdOrderByChunkIndex(id).map { it.toResponse() }
        } else {
            null
        }
        
        return document.toDetailResponse(chunks)
    }

    /**
     * List documents for a tenant.
     */
    fun listDocuments(
        tenantId: String,
        status: ProcessingStatus? = null,
        pageable: Pageable
    ): Page<DocumentResponse> {
        val page = if (status != null) {
            documentRepository.findByTenantIdAndStatus(tenantId, status, pageable)
        } else {
            documentRepository.findByTenantId(tenantId, pageable)
        }
        
        return page.map { it.toResponse() }
    }

    /**
     * Delete a document and all associated data.
     *
     * NOT annotated @Transactional — see processDocument for the rationale.
     * DB deletions happen in a separate @Transactional helper after the HTTP call.
     */
    suspend fun deleteDocument(id: UUID, tenantId: String): Boolean {
        val exists = documentExistsForTenant(id, tenantId)
        if (!exists) return false

        // Delete vectors first — prevents orphan vector/PG state mismatch.
        val vectorsDeleted = ingestionServiceClient.deleteDocumentVectors(tenantId, id)
        if (!vectorsDeleted) {
            throw IllegalStateException(
                "Failed to delete vectors for document $id in Qdrant. " +
                "Aborting deletion to prevent orphan vector/PG state mismatch."
            )
        }

        deleteDocumentRecords(id, tenantId)
        return true
    }

    @Transactional(readOnly = true)
    fun documentExistsForTenant(id: UUID, tenantId: String): Boolean =
        documentRepository.findByIdAndTenantId(id, tenantId) != null

    @Transactional
    fun deleteDocumentRecords(id: UUID, tenantId: String) {
        chunkRepository.deleteByDocumentId(id)
        try {
            storageService.deleteDocumentFiles(tenantId, id)
        } catch (e: Exception) {
            logger.warn("Failed to delete files for document $id (non-fatal): ${e.message}")
        }
        documentRepository.findByIdAndTenantId(id, tenantId)?.let { documentRepository.delete(it) }
    }

    /**
     * Get chunks for a document.
     */
    fun getDocumentChunks(documentId: UUID, tenantId: String): List<ChunkResponse> {
        documentRepository.findByIdAndTenantId(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found")

        return chunkRepository.findByDocumentIdOrderByChunkIndex(documentId)
            .map { it.toResponse() }
    }

    /**
     * Bulk create document records (for sample datasets).
     */
    @Transactional
    fun bulkCreateDocuments(request: BulkDocumentCreateRequest): BulkDocumentCreateResponse {
        val documentIds = mutableListOf<UUID>()
        
        for (item in request.documents) {
            val documentId = UUID.randomUUID()
            
            val metadata = item.metadata.toMutableMap()
            metadata["domain"] = item.domain
            metadata["document_type"] = item.domain
            metadata["source"] = "sample_dataset"
            item.content?.let { metadata["content_preview"] = it.take(500) }
            
            val document = Document(
                id = documentId,
                tenantId = request.tenantId,
                filename = item.filename,
                contentType = "text/plain",
                fileSize = item.content?.length?.toLong() ?: 0L,
                filePath = "",
                status = ProcessingStatus.COMPLETED,
                chunkCount = item.chunkCount,
                metadata = metadata
            )
            
            documentRepository.save(document)
            documentIds.add(documentId)
        }
        
        logger.info("Bulk created ${documentIds.size} document records for tenant ${request.tenantId}")
        
        return BulkDocumentCreateResponse(
            created = documentIds.size,
            documentIds = documentIds
        )
    }

    /**
     * Delete all documents for a tenant in pages.
     *
     * NOT annotated @Transactional — see processDocument for the rationale.
     * Each batch delete is performed in its own @Transactional helper.
     */
    suspend fun deleteAllDocuments(tenantId: String): Int {
        try {
            ingestionServiceClient.deleteTenantVectors(tenantId)
        } catch (e: Exception) {
            logger.warn("Failed to delete vectors for tenant $tenantId: ${e.message}")
        }

        val deleted = deleteTenantDocumentBatches(tenantId)
        logger.info("Deleted $deleted documents for tenant $tenantId")
        return deleted
    }

    @Transactional
    fun deleteTenantDocumentBatches(tenantId: String): Int {
        val pageSize = 200
        var deleted = 0
        var page = 0

        while (true) {
            val documents = documentRepository.findByTenantId(
                tenantId, PageRequest.of(page, pageSize)
            ).content
            if (documents.isEmpty()) break

            for (doc in documents) {
                chunkRepository.deleteByDocumentId(doc.id)
                try {
                    storageService.deleteDocumentFiles(tenantId, doc.id)
                } catch (_: Exception) { }
            }
            documentRepository.deleteAll(documents)
            deleted += documents.size
            page++
        }
        return deleted
    }

    // Extension functions for mapping
    private fun Document.toResponse() = DocumentResponse(
        id = id,
        filename = filename,
        contentType = contentType,
        fileSize = fileSize,
        chunkCount = chunkCount,
        status = status,
        metadata = metadata,
        createdAt = createdAt,
        updatedAt = updatedAt
    )

    private fun Document.toDetailResponse(chunks: List<ChunkResponse>?) = DocumentDetailResponse(
        id = id,
        filename = filename,
        contentType = contentType,
        fileSize = fileSize,
        chunkCount = chunkCount,
        status = status,
        metadata = metadata,
        errorMessage = errorMessage,
        chunks = chunks,
        createdAt = createdAt,
        updatedAt = updatedAt
    )

    private fun Chunk.toResponse() = ChunkResponse(
        id = id,
        chunkIndex = chunkIndex,
        content = content,
        startChar = startChar,
        endChar = endChar,
        tokenCount = tokenCount,
        metadata = metadata
    )
}
