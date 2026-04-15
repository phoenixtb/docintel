package com.docintel.document.service

import com.docintel.document.dto.*
import com.docintel.document.entity.Chunk
import com.docintel.document.entity.DataSource
import com.docintel.document.entity.DataSourceStatus
import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.repository.ChunkRepository
import com.docintel.document.repository.DataSourceRepository
import com.docintel.document.repository.DocumentRepository
import com.docintel.document.sse.DocumentStatusEvent
import org.slf4j.LoggerFactory
import org.springframework.context.ApplicationEventPublisher
import org.springframework.data.domain.Page
import org.springframework.data.domain.PageRequest
import org.springframework.data.domain.Pageable
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional
import org.springframework.web.multipart.MultipartFile
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.time.Instant
import java.util.UUID

@Service
class DocumentService(
    private val documentRepository: DocumentRepository,
    private val dataSourceRepository: DataSourceRepository,
    private val chunkRepository: ChunkRepository,
    private val storageService: StorageService,
    private val ingestionServiceClient: IngestionServiceClient,
    private val eventPublisher: ApplicationEventPublisher,
) {
    private val logger = LoggerFactory.getLogger(DocumentService::class.java)

    companion object {
        /**
         * Derive a deterministic UUID and full content hash from SHA-256(tenantId + fileBytes).
         *
         * UUID uses the first 16 bytes of the digest (128-bit space; effectively collision-free).
         * The full 64-char hex digest is returned separately and stored in content_hash
         * for observability and secondary dedup lookups.
         *
         * @return Pair(documentId UUID, contentHash 64-char hex string)
         */
        fun computeContentId(tenantId: String, fileBytes: ByteArray): Pair<UUID, String> {
            val digest = MessageDigest.getInstance("SHA-256")
            digest.update(tenantId.toByteArray(Charsets.UTF_8))
            digest.update(fileBytes)
            val hash = digest.digest()
            val hex = hash.joinToString("") { "%02x".format(it) }
            val uuid = uuidFromHashBytes(hash)
            return Pair(uuid, hex)
        }

        /**
         * Reconstruct a document UUID from its 64-char content_hash hex string.
         * Used by from-path endpoint where the hash is provided by the data-loader.
         */
        fun uuidFromContentHash(contentHash: String): UUID {
            require(contentHash.length >= 32) { "content_hash must be at least 32 hex chars" }
            val bytes = ByteArray(16) { i ->
                ((Character.digit(contentHash[i * 2], 16) shl 4) + Character.digit(contentHash[i * 2 + 1], 16)).toByte()
            }
            return uuidFromHashBytes(bytes)
        }

        private fun uuidFromHashBytes(hash: ByteArray): UUID = UUID(
            ByteBuffer.wrap(hash, 0, 8).long,
            ByteBuffer.wrap(hash, 8, 8).long
        )
    }

    /**
     * Upload and store a document, then return the saved record.
     * Caller is responsible for triggering ingestion via processDocument().
     *
     * Dedup semantics on content_hash collision:
     *  - COMPLETED  → return existing (second value = true = deduplicated)
     *  - PROCESSING → return in-flight record (deduplicated)
     *  - FAILED     → re-upload (idempotent MinIO PUT) + return freshly saved record
     *  - PENDING    → return existing (sweeper will re-trigger)
     */
    @Transactional
    fun uploadDocument(
        file: MultipartFile,
        tenantId: String,
        metadata: Map<String, String> = emptyMap()
    ): Pair<DocumentResponse, Boolean> {
        val fileBytes = file.bytes
        val (documentId, contentHash) = computeContentId(tenantId, fileBytes)

        val existing = documentRepository.findByIdAndTenantId(documentId, tenantId)
        if (existing != null) {
            when (existing.status) {
                ProcessingStatus.COMPLETED, ProcessingStatus.PROCESSING, ProcessingStatus.PENDING -> {
                    logger.info(
                        "Dedup hit ({}): document_id={} tenant={}",
                        existing.status, documentId, tenantId
                    )
                    return Pair(existing.toResponse(), true)
                }
                ProcessingStatus.FAILED -> {
                    logger.info("Re-processing FAILED document: document_id={} tenant={}", documentId, tenantId)
                    // Fall through to re-upload and re-save below.
                }
            }
        }

        // MinIO PUT is idempotent for content-addressable paths.
        val filePath = storageService.storeFile(file, tenantId, contentHash)
        val safeFilename = java.nio.file.Paths.get(file.originalFilename ?: "unknown").fileName.toString()

        val document = Document(
            id = documentId,
            tenantId = tenantId,
            filename = safeFilename,
            contentType = file.contentType,
            fileSize = file.size,
            filePath = filePath,
            status = ProcessingStatus.PENDING,
            metadata = metadata,
            contentHash = contentHash,
            dataSourceId = null
        )

        val saved = documentRepository.save(document)
        eventPublisher.publishEvent(DocumentStatusEvent(
            documentId = saved.id.toString(),
            tenantId   = tenantId,
            status     = "PENDING",
            stage      = "Queued",
            filename   = safeFilename,
        ))
        return Pair(saved.toResponse(), false)
    }

    /**
     * Register a document that already exists in MinIO at the content-addressable path.
     *
     * Called internally by data-loader after it has uploaded file bytes. The document_id
     * is derived from content_hash (same algorithm as uploadDocument) so dedup is automatic.
     *
     * Requires internal service token (enforced at gateway / GatewayAuthFilter level).
     */
    @Transactional
    fun registerFromPath(request: FromPathRequest, tenantId: String): Pair<DocumentResponse, Boolean> {
        val documentId = uuidFromContentHash(request.contentHash)

        val existing = documentRepository.findByIdAndTenantId(documentId, tenantId)
        if (existing != null) {
            when (existing.status) {
                ProcessingStatus.COMPLETED, ProcessingStatus.PROCESSING, ProcessingStatus.PENDING -> {
                    logger.info(
                        "Dedup hit ({}) from-path: document_id={} tenant={}",
                        existing.status, documentId, tenantId
                    )
                    return Pair(existing.toResponse(), true)
                }
                ProcessingStatus.FAILED -> {
                    logger.info("Re-processing FAILED document from-path: document_id={} tenant={}", documentId, tenantId)
                }
            }
        }

        val document = Document(
            id = documentId,
            tenantId = tenantId,
            filename = request.filename,
            contentType = request.contentType,
            fileSize = request.fileSize,
            filePath = request.minioPath,
            status = ProcessingStatus.PENDING,
            metadata = request.metadata,
            contentHash = request.contentHash,
            dataSourceId = request.dataSourceId
        )

        val saved = documentRepository.save(document)
        return Pair(saved.toResponse(), false)
    }

    /**
     * Trigger ingestion for a document.
     *
     * NOT annotated @Transactional because the function suspends inside triggerIngestion
     * (WebClient HTTP call), which would cause the Spring ThreadLocal transaction context
     * to be lost when the coroutine resumes on a different thread.
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
            try {
                storageService.deleteDocumentFiles(tenantId, doc.filePath)
            } catch (se: Exception) {
                logger.warn("Could not clean up MinIO files for FAILED document {}: {}", documentId, se.message)
            }
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
        eventPublisher.publishEvent(DocumentStatusEvent(
            documentId = documentId.toString(),
            tenantId   = tenantId,
            status     = "PROCESSING",
            stage      = "Processing",
            filename   = doc.filename,
        ))
    }

    @Transactional
    fun markDocumentCompleted(documentId: UUID, tenantId: String, chunkCount: Int) {
        val doc = documentRepository.findByIdAndTenantId(documentId, tenantId) ?: return
        doc.status = ProcessingStatus.COMPLETED
        doc.chunkCount = chunkCount
        documentRepository.save(doc)
        eventPublisher.publishEvent(DocumentStatusEvent(
            documentId = documentId.toString(),
            tenantId   = tenantId,
            status     = "COMPLETED",
            stage      = "Indexed",
            filename   = doc.filename,
            chunkCount = chunkCount,
        ))
    }

    @Transactional
    fun markDocumentFailed(documentId: UUID, tenantId: String, message: String?) {
        val doc = documentRepository.findByIdAndTenantId(documentId, tenantId) ?: return
        doc.status = ProcessingStatus.FAILED
        doc.errorMessage = message
        documentRepository.save(doc)
        eventPublisher.publishEvent(DocumentStatusEvent(
            documentId   = documentId.toString(),
            tenantId     = tenantId,
            status       = "FAILED",
            stage        = "Failed",
            filename     = doc.filename,
            errorMessage = message,
        ))
    }

    @Transactional(readOnly = true)
    fun listInFlight(tenantId: String): List<Document> =
        documentRepository.findByTenantIdAndStatusIn(
            tenantId, listOf(ProcessingStatus.PENDING, ProcessingStatus.PROCESSING)
        )

    fun getDocument(id: UUID, tenantId: String, includeChunks: Boolean = false): DocumentDetailResponse? {
        val document = documentRepository.findByIdAndTenantId(id, tenantId) ?: return null

        val chunks = if (includeChunks) {
            chunkRepository.findByDocumentIdOrderByChunkIndex(id).map { it.toResponse() }
        } else {
            null
        }

        return document.toDetailResponse(chunks)
    }

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
     */
    suspend fun deleteDocument(id: UUID, tenantId: String): Boolean {
        val doc = fetchDocumentForIngestion(id, tenantId) ?: return false

        val vectorsDeleted = ingestionServiceClient.deleteDocumentVectors(tenantId, id)
        if (!vectorsDeleted) {
            throw IllegalStateException(
                "Failed to delete vectors for document $id in Qdrant. " +
                "Aborting deletion to prevent orphan vector/PG state mismatch."
            )
        }

        deleteDocumentRecords(id, tenantId, doc.filePath)
        return true
    }

    @Transactional(readOnly = true)
    fun documentExistsForTenant(id: UUID, tenantId: String): Boolean =
        documentRepository.findByIdAndTenantId(id, tenantId) != null

    @Transactional
    fun deleteDocumentRecords(id: UUID, tenantId: String, filePath: String) {
        chunkRepository.deleteByDocumentId(id)
        try {
            storageService.deleteDocumentFiles(tenantId, filePath)
        } catch (e: Exception) {
            logger.warn("Failed to delete files for document $id (non-fatal): ${e.message}")
        }
        documentRepository.findByIdAndTenantId(id, tenantId)?.let { documentRepository.delete(it) }
    }

    fun getDocumentChunks(documentId: UUID, tenantId: String): List<ChunkResponse> {
        documentRepository.findByIdAndTenantId(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found")

        return chunkRepository.findByDocumentIdOrderByChunkIndex(documentId)
            .map { it.toResponse() }
    }

    /**
     * Delete all documents for a tenant.
     * NOT annotated @Transactional — see processDocument for the rationale.
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

        while (true) {
            val documents = documentRepository.findByTenantId(
                tenantId, PageRequest.of(0, pageSize)
            ).content
            if (documents.isEmpty()) break

            for (doc in documents) {
                chunkRepository.deleteByDocumentId(doc.id)
                try {
                    storageService.deleteDocumentFiles(tenantId, doc.filePath)
                } catch (_: Exception) { }
            }
            documentRepository.deleteAll(documents)
            deleted += documents.size
        }
        return deleted
    }

    // ==========================================================================
    // Chunk bulk persist — called by ingestion-service instead of direct DB write
    // ==========================================================================

    /**
     * Persist a batch of chunks produced by ingestion-service.
     *
     * Validates document ownership, upserts chunks via JPA saveAll,
     * and updates the document's chunk_count in one transaction.
     * Replaces the direct psycopg2 INSERT that ingestion-service used to perform.
     */
    @Transactional
    fun bulkPersistChunks(documentId: UUID, tenantId: String, requests: List<ChunkPersistRequest>): Int {
        val doc = documentRepository.findByIdAndTenantId(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found or tenant mismatch: $documentId")

        val entities = requests.map { req ->
            Chunk(
                id          = req.chunkId,
                documentId  = documentId,
                tenantId    = tenantId,
                content     = req.content,
                chunkIndex  = req.chunkIndex,
                startChar   = req.startChar,
                endChar     = req.endChar,
                tokenCount  = req.tokenCount,
                metadata    = req.metadata,
            )
        }
        chunkRepository.saveAll(entities)

        doc.chunkCount = requests.size
        doc.status = ProcessingStatus.COMPLETED
        documentRepository.save(doc)

        eventPublisher.publishEvent(DocumentStatusEvent(
            documentId = documentId.toString(),
            tenantId   = tenantId,
            status     = "COMPLETED",
            stage      = "Indexed",
            filename   = doc.filename,
            chunkCount = requests.size,
        ))

        logger.info("Bulk persisted {} chunks for document {} (tenant={})", requests.size, documentId, tenantId)
        return requests.size
    }

    // ==========================================================================
    // DataSource lifecycle
    // ==========================================================================

    @Transactional
    fun createDataSource(tenantId: String, request: DataSourceRequest): DataSourceResponse {
        val dataSource = DataSource(
            id = UUID.randomUUID(),
            tenantId = tenantId,
            sourceType = request.sourceType,
            sourceConfig = request.sourceConfig,
            status = DataSourceStatus.LOADING
        )
        return dataSourceRepository.save(dataSource).toResponse()
    }

    @Transactional
    fun completeDataSource(id: UUID, tenantId: String, documentCount: Int): DataSourceResponse {
        val dataSource = dataSourceRepository.findByIdAndTenantId(id, tenantId)
            ?: throw IllegalArgumentException("DataSource not found: $id")
        dataSource.status = DataSourceStatus.COMPLETED
        dataSource.documentCount = documentCount
        dataSource.completedAt = Instant.now()
        return dataSourceRepository.save(dataSource).toResponse()
    }

    @Transactional
    fun failDataSource(id: UUID, tenantId: String): DataSourceResponse {
        val dataSource = dataSourceRepository.findByIdAndTenantId(id, tenantId)
            ?: throw IllegalArgumentException("DataSource not found: $id")
        dataSource.status = DataSourceStatus.FAILED
        dataSource.completedAt = Instant.now()
        return dataSourceRepository.save(dataSource).toResponse()
    }

    @Transactional(readOnly = true)
    fun getDataSource(id: UUID, tenantId: String): DataSourceResponse? =
        dataSourceRepository.findByIdAndTenantId(id, tenantId)?.toResponse()

    @Transactional(readOnly = true)
    fun listDataSources(tenantId: String): List<DataSourceResponse> =
        dataSourceRepository.findByTenantId(tenantId).map { it.toResponse() }

    // ==========================================================================
    // Private mapping helpers
    // ==========================================================================

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

    private fun DataSource.toResponse() = DataSourceResponse(
        id = id,
        tenantId = tenantId,
        sourceType = sourceType,
        sourceConfig = sourceConfig,
        status = status,
        documentCount = documentCount,
        createdAt = createdAt,
        completedAt = completedAt
    )
}
