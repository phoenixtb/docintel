package com.docintel.document.service

import com.docintel.document.dto.*
import com.docintel.document.entity.Chunk
import com.docintel.document.entity.DataSource
import com.docintel.document.entity.DataSourceStatus
import com.docintel.document.entity.DeletionTask
import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.messaging.DocumentReadyEvent
import com.docintel.document.messaging.DocumentStreamPublisher
import com.docintel.document.repository.ChunkRepository
import com.docintel.document.repository.DataSourceRepository
import com.docintel.document.repository.DeletionTaskRepository
import com.docintel.document.repository.DocumentRepository
import com.docintel.document.sse.DocumentStatusEvent
import org.slf4j.LoggerFactory
import org.springframework.context.ApplicationEventPublisher
import org.springframework.data.domain.Page
import org.springframework.data.domain.PageRequest
import org.springframework.data.domain.Pageable
import org.springframework.data.domain.Sort
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
    private val deletionTaskRepository: DeletionTaskRepository,
    private val storageService: StorageService,
    private val vectorStoreClient: VectorStoreClient,
    private val streamPublisher: DocumentStreamPublisher,
    private val eventPublisher: ApplicationEventPublisher,
) {
    private val logger = LoggerFactory.getLogger(DocumentService::class.java)

    companion object {
        /** Maximum IDs fetched in a single cleanup snapshot. Protects memory on huge tenants. */
        const val MAX_SNAPSHOT_IDS = 10_000

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
                ProcessingStatus.FAILED, ProcessingStatus.DELETING -> {
                    // DELETING: a prior delete is in progress; treat as gone and allow re-upload.
                    logger.info("Re-processing {} document: document_id={} tenant={}", existing.status, documentId, tenantId)
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
                ProcessingStatus.FAILED, ProcessingStatus.DELETING -> {
                    logger.info("Re-processing {} document from-path: document_id={} tenant={}", existing.status, documentId, tenantId)
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
     * Trigger ingestion for a document by publishing to the [documents.ready] Redis stream.
     *
     * The stream worker in ingestion-service picks up the event and runs the full pipeline.
     * This eliminates the direct REST /ingest call, giving a single ingress path for both
     * user uploads and data-loader documents.
     */
    fun processDocument(
        documentId: UUID,
        tenantId: String,
        domainHint: String = "auto"
    ): ProcessingResult {
        val doc = fetchDocumentForIngestion(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found: $documentId")

        markDocumentProcessing(documentId, tenantId)

        return try {
            streamPublisher.publishDocumentReady(
                DocumentReadyEvent(
                    documentId = documentId.toString(),
                    tenantId   = tenantId,
                    bucket     = "docintel-$tenantId",
                    objectPath = doc.filePath,
                    filename   = doc.filename,
                    domainHint = if (domainHint.isBlank()) "auto" else domainHint,
                    metadata   = doc.metadata.mapValues { it.value }
                )
            )
            logger.info(
                "Queued for ingestion via stream: document_id={} tenant={} file={}",
                documentId, tenantId, doc.filename
            )
            ProcessingResult(documentId = documentId, chunkCount = 0, status = ProcessingStatus.PROCESSING)

        } catch (e: Exception) {
            logger.error("Failed to queue document {} for ingestion: {}", documentId, e.message, e)
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
        // DELETING is an internal state; never expose it to callers.
        if (status == ProcessingStatus.DELETING) return org.springframework.data.domain.Page.empty(pageable)
        val page = if (status != null) {
            documentRepository.findByTenantIdAndStatus(tenantId, status, pageable)
        } else {
            documentRepository.findByTenantIdAndStatusNot(tenantId, ProcessingStatus.DELETING, pageable)
        }
        return page.map { it.toResponse() }
    }

    /**
     * Mark a document for async deletion.
     *
     * Atomically sets status=DELETING and inserts a [DeletionTask] outbox record.
     * [DeletionTaskWorker] will asynchronously clean up Qdrant vectors, MinIO files,
     * and finally the PG rows. Returns false if the document does not exist.
     */
    @Transactional
    fun markForDeletion(id: UUID, tenantId: String): Boolean {
        val doc = documentRepository.findByIdAndTenantId(id, tenantId) ?: return false
        if (doc.status == ProcessingStatus.DELETING) return true  // already queued
        doc.status = ProcessingStatus.DELETING
        documentRepository.save(doc)
        deletionTaskRepository.save(
            DeletionTask(tenantId = tenantId, documentId = id, filePath = doc.filePath)
        )
        logger.info("Document {} (tenant={}) queued for deletion", id, tenantId)
        return true
    }

    @Transactional(readOnly = true)
    fun documentExistsForTenant(id: UUID, tenantId: String): Boolean =
        documentRepository.findByIdAndTenantId(id, tenantId) != null

    /**
     * Directly delete document records from PG (called by [DeletionTaskWorker] after
     * Qdrant + MinIO have been cleaned up). Each repository call has its own
     * [Transactional] and is idempotent — safe to retry.
     */
    fun deleteDocumentRecords(id: UUID, tenantId: String) {
        chunkRepository.deleteByDocumentId(id)
        documentRepository.deleteByIdAndTenantId(id, tenantId)
    }

    fun getDocumentChunks(documentId: UUID, tenantId: String): List<ChunkResponse> {
        documentRepository.findByIdAndTenantId(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found")

        return chunkRepository.findByDocumentIdOrderByChunkIndex(documentId)
            .map { it.toResponse() }
    }

    /**
     * Mark all documents for a tenant for async deletion.
     *
     * Optimistically drops the entire Qdrant collection (fast path). Then marks each
     * document DELETING and inserts a [DeletionTask] with [DeletionTask.qdrantDone]
     * set based on whether the Qdrant collection drop succeeded.
     * [DeletionTaskWorker] then handles per-document MinIO cleanup and PG removal.
     */
    suspend fun deleteAllDocuments(tenantId: String): Int {
        val qdrantDone = try {
            vectorStoreClient.deleteTenantVectors(tenantId)
        } catch (e: Exception) {
            logger.warn("Failed to delete Qdrant collection for tenant {}: {}", tenantId, e.message)
            false
        }
        val count = markAllDocumentsForDeletion(tenantId, qdrantAlreadyDeleted = qdrantDone)
        logger.info("Queued {} documents for deletion for tenant={} (qdrantCollectionDropped={})", count, tenantId, qdrantDone)
        return count
    }

    @Transactional
    fun markAllDocumentsForDeletion(tenantId: String, qdrantAlreadyDeleted: Boolean = false): Int {
        var total = 0
        while (true) {
            val page = documentRepository.findByTenantIdAndStatusNot(
                tenantId, ProcessingStatus.DELETING, PageRequest.of(0, 200)
            )
            if (page.content.isEmpty()) break
            val tasks = page.content.map { doc ->
                doc.status = ProcessingStatus.DELETING
                DeletionTask(
                    tenantId = tenantId,
                    documentId = doc.id,
                    filePath = doc.filePath,
                    qdrantDone = qdrantAlreadyDeleted,
                )
            }
            documentRepository.saveAll(page.content)
            deletionTaskRepository.saveAll(tasks)
            total += page.content.size
            if (!page.hasNext()) break
        }
        return total
    }

    // ==========================================================================
    // Cleanup — filter-based preview and ID snapshot (used by CleanupJobService)
    // ==========================================================================

    /** Returns the number of documents matching [filters] for the given tenant. */
    @Transactional(readOnly = true)
    fun previewCleanup(tenantId: String, filters: CleanupFiltersRequest): Long {
        val spec = DocumentSpecifications.fromFilters(tenantId, filters)
        return documentRepository.count(spec)
    }

    /**
     * Snapshot the IDs of documents matching [filters].
     * Paginates through the result to cap memory usage at [MAX_SNAPSHOT_IDS].
     * The snapshot is stable: subsequent uploads won't appear in this job's work set.
     */
    @Transactional(readOnly = true)
    fun snapshotMatchingIds(tenantId: String, filters: CleanupFiltersRequest): List<UUID> {
        val spec = DocumentSpecifications.fromFilters(tenantId, filters)
        val ids = mutableListOf<UUID>()
        val batchSize = 500
        val sort = Sort.by(Sort.Direction.ASC, "createdAt")
        var page = 0
        while (ids.size < MAX_SNAPSHOT_IDS) {
            val batch = documentRepository.findAll(spec, PageRequest.of(page++, batchSize, sort))
            ids.addAll(batch.content.map { it.id })
            if (!batch.hasNext()) break
        }
        return ids.take(MAX_SNAPSHOT_IDS)
    }

    // ==========================================================================
    // Chunk bulk persist — called by ingestion-service instead of direct DB write
    // ==========================================================================

    /**
     * Persist a batch of chunks produced by ingestion-service (replaces the full chunk set).
     *
     * Clears any existing chunks for this document, saves the new set, and returns the count.
     * Does NOT flip the document status — [IngestionCompleteConsumer] is the sole authority
     * for terminal status transitions (COMPLETED / FAILED), preventing the double-COMPLETED bug.
     */
    @Transactional
    fun bulkPersistChunks(documentId: UUID, tenantId: String, requests: List<ChunkPersistRequest>): Int {
        documentRepository.findByIdAndTenantId(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found or tenant mismatch: $documentId")

        chunkRepository.deleteByDocumentId(documentId)

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

        logger.info("Bulk persisted {} chunks for document {} (tenant={})", requests.size, documentId, tenantId)
        return requests.size
    }

    /**
     * Append a batch of chunks for a document shard (used by the page-sharded PDF pipeline).
     *
     * Unlike [bulkPersistChunks], this does NOT clear existing chunks — it additively upserts.
     * Used during multi-shard ingestion where each shard appends its chunks independently.
     * Pass [clearExisting]=true only on the first shard of a fresh ingestion run to remove
     * any stale chunks from a prior failed attempt.
     *
     * Status transition is NOT performed here; the caller publishes [ingestion.complete] after
     * the final shard and [IngestionCompleteConsumer] drives the terminal status update.
     */
    @Transactional
    fun appendChunks(
        documentId: UUID,
        tenantId: String,
        requests: List<ChunkPersistRequest>,
        clearExisting: Boolean = false,
    ): Int {
        documentRepository.findByIdAndTenantId(documentId, tenantId)
            ?: throw IllegalArgumentException("Document not found or tenant mismatch: $documentId")

        if (clearExisting) {
            chunkRepository.deleteByDocumentId(documentId)
        }

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

        logger.debug("Appended {} chunks (clearExisting={}) for document {} (tenant={})",
            requests.size, clearExisting, documentId, tenantId)
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

    fun getStats(tenantId: String): DocumentStatsResponse {
        val byStatus = documentRepository.countByStatusForTenant(tenantId)
            .associate { row -> (row[0] as ProcessingStatus).name to (row[1] as Long) }
        val byDomain = documentRepository.countByDomainForTenant(tenantId)
            .associate { row -> row[0].toString() to (row[1] as Number).toLong() }
        val bySource = documentRepository.countBySourceForTenant(tenantId)
            .associate { row -> row[0].toString() to (row[1] as Number).toLong() }
        val totalBytes = documentRepository.sumFileSizeForTenant(tenantId)
        val totalChunks = documentRepository.sumChunkCountForTenant(tenantId)
        val latestTs = documentRepository.findLatestCreatedAtForTenant(tenantId)
        val lastUploadedAt = latestTs?.toInstant()
        val totalDocuments = byStatus.values.sum()
        return DocumentStatsResponse(
            totalDocuments = totalDocuments,
            totalChunks = totalChunks,
            totalBytes = totalBytes,
            byStatus = byStatus,
            byDomain = byDomain,
            bySource = bySource,
            lastUploadedAt = lastUploadedAt,
        )
    }

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
