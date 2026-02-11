package com.docintel.document.service

import com.docintel.document.dto.*
import com.docintel.document.entity.Chunk
import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.repository.ChunkRepository
import com.docintel.document.repository.DocumentRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.slf4j.LoggerFactory
import org.springframework.data.domain.Page
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
    private val textExtractionService: TextExtractionService,
    private val ragServiceClient: RagServiceClient
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
        
        // Store file in MinIO
        val filePath = storageService.storeFile(file, tenantId, documentId)
        
        // Create document record
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
     * Process a document: extract text, classify domain, chunk, and index.
     * This is called asynchronously after upload.
     *
     * @param domainHint: Domain hint from upload
     *   - "auto" or empty: Use zero-shot classification
     *   - other: Use specified domain directly
     */
    @Transactional
    suspend fun processDocument(
        documentId: UUID,
        tenantId: String,
        domainHint: String = "auto"
    ): ProcessingResult {
        val document = documentRepository.findByIdAndTenantId(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found: $documentId")

        try {
            // Update status to processing
            document.status = ProcessingStatus.PROCESSING
            documentRepository.save(document)

            // Extract text from file
            val fileStream = storageService.getFile(document.filePath)
            val extraction = withContext(Dispatchers.IO) {
                textExtractionService.extractText(fileStream, document.filename)
            }

            if (extraction.text.isBlank()) {
                throw IllegalStateException("No text extracted from document")
            }

            logger.info("Extracted ${extraction.text.length} characters from ${document.filename}")

            // Determine domain - either use hint or auto-classify
            val domain = if (domainHint.isBlank() || domainHint == "auto") {
                try {
                    val classifyResult = ragServiceClient.classifyDomain(extraction.text)
                    logger.info("Auto-classified ${document.filename} as ${classifyResult.domain} (confidence: ${classifyResult.confidence})")
                    classifyResult.domain
                } catch (e: Exception) {
                    logger.warn("Domain classification failed, using 'general': ${e.message}")
                    "general"
                }
            } else {
                domainHint
            }

            // Build metadata with domain
            val metadataWithDomain = document.metadata.toMutableMap()
            metadataWithDomain["domain"] = domain
            metadataWithDomain["document_type"] = domain

            // Send to RAG service for chunking
            val chunkResponse = ragServiceClient.chunkText(
                text = extraction.text,
                documentId = documentId,
                tenantId = tenantId,
                filename = document.filename,
                metadata = metadataWithDomain.mapValues { it.value as Any }
            )

            logger.info("Created ${chunkResponse.chunkCount} chunks for ${document.filename}")

            // Save chunks to database
            val chunks = chunkResponse.chunks.mapIndexed { index, chunk ->
                Chunk(
                    id = UUID.fromString(chunk.chunkId),
                    documentId = documentId,
                    tenantId = tenantId,
                    content = chunk.content,
                    chunkIndex = index,
                    startChar = chunk.startChar,
                    endChar = chunk.endChar,
                    tokenCount = chunk.tokenCount,
                    metadata = chunk.metadata + mapOf(
                        "filename" to document.filename,
                        "document_type" to domain,
                        "domain" to domain
                    )
                )
            }
            chunkRepository.saveAll(chunks)

            // Send chunks to RAG service for embedding
            val chunkData = chunks.map { chunk ->
                ChunkData(
                    chunkId = chunk.id.toString(),
                    content = chunk.content,
                    metadata = chunk.metadata + mapOf(
                        "chunk_index" to chunk.chunkIndex,
                        "filename" to document.filename,
                        "domain" to domain,
                        "document_type" to domain
                    )
                )
            }

            val indexResponse = ragServiceClient.indexChunks(
                chunks = chunkData,
                documentId = documentId,
                tenantId = tenantId
            )

            logger.info("Indexed ${indexResponse.embeddedCount} chunks for ${document.filename}")

            // Update document status
            document.status = ProcessingStatus.COMPLETED
            document.chunkCount = chunks.size
            documentRepository.save(document)

            return ProcessingResult(
                documentId = documentId,
                chunkCount = chunks.size,
                status = ProcessingStatus.COMPLETED
            )

        } catch (e: Exception) {
            logger.error("Failed to process document $documentId: ${e.message}", e)
            
            document.status = ProcessingStatus.FAILED
            document.errorMessage = e.message
            documentRepository.save(document)

            return ProcessingResult(
                documentId = documentId,
                chunkCount = 0,
                status = ProcessingStatus.FAILED,
                errorMessage = e.message
            )
        }
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
     */
    @Transactional
    suspend fun deleteDocument(id: UUID, tenantId: String): Boolean {
        val document = documentRepository.findByIdAndTenantId(id, tenantId) ?: return false

        // Delete vectors from RAG service
        try {
            ragServiceClient.deleteDocumentVectors(tenantId, id)
        } catch (e: Exception) {
            logger.warn("Failed to delete vectors for document $id: ${e.message}")
        }

        // Delete chunks from database
        chunkRepository.deleteByDocumentId(id)

        // Delete file from storage
        try {
            storageService.deleteDocumentFiles(tenantId, id)
        } catch (e: Exception) {
            logger.warn("Failed to delete files for document $id: ${e.message}")
        }

        // Delete document record
        documentRepository.delete(document)

        return true
    }

    /**
     * Get chunks for a document.
     */
    fun getDocumentChunks(documentId: UUID, tenantId: String): List<ChunkResponse> {
        val document = documentRepository.findByIdAndTenantId(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found")
        
        return chunkRepository.findByDocumentIdOrderByChunkIndex(documentId)
            .map { it.toResponse() }
    }

    /**
     * Bulk create document records (for sample datasets).
     * Creates document records in PostgreSQL without file upload.
     * The actual content is already indexed in Qdrant by the RAG service.
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
            
            val document = Document(
                id = documentId,
                tenantId = request.tenantId,
                filename = item.filename,
                contentType = "text/plain",
                fileSize = item.content?.length?.toLong() ?: 0L,
                filePath = "",  // No file stored for sample datasets
                status = ProcessingStatus.COMPLETED,  // Already indexed in Qdrant
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
