package com.docintel.document.controller

import com.docintel.document.dto.*
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.service.DocumentService
import com.docintel.document.sse.SseEmitterRegistry
import com.fasterxml.jackson.databind.ObjectMapper
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.slf4j.LoggerFactory
import org.springframework.data.domain.Page
import org.springframework.data.domain.Pageable
import org.springframework.data.web.PageableDefault
import org.springframework.http.HttpStatus
import org.springframework.http.MediaType
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.*
import org.springframework.web.multipart.MultipartFile
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter
import java.util.UUID

@RestController
@RequestMapping("/internal/documents")
class DocumentController(
    private val documentService: DocumentService,
    private val objectMapper: ObjectMapper,
    private val sseRegistry: SseEmitterRegistry,
) {
    private val logger = LoggerFactory.getLogger(DocumentController::class.java)
    private val processingScope = CoroutineScope(
        SupervisorJob() + Dispatchers.Default +
        CoroutineExceptionHandler { _, t -> logger.error("Background processing error", t) }
    )

    /**
     * Upload a new document (browser multipart upload path).
     *
     * Computes content hash → dedup check → stores to MinIO at content-addressable path
     * → triggers async ingestion. Deduplicated documents return 200 (already exists),
     * new documents return 201.
     */
    @PostMapping(consumes = [MediaType.MULTIPART_FORM_DATA_VALUE])
    fun uploadDocument(
        @RequestParam("file") file: MultipartFile,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestParam("domain", defaultValue = "auto") domain: String,
        @RequestParam("metadata", required = false) metadataJson: String?,
        @RequestHeader("Idempotency-Key", required = false) idempotencyKey: String?
    ): ResponseEntity<DocumentResponse> {
        val metadata = parseMetadata(metadataJson).toMutableMap()
        if (domain.isNotBlank() && domain != "auto") {
            metadata["domain_hint"] = domain
        }
        idempotencyKey?.let { metadata["idempotency_key"] = it }

        val (document, deduplicated) = documentService.uploadDocument(file, tenantId, metadata)

        if (!deduplicated) {
            processingScope.launch {
                documentService.processDocument(document.id, tenantId, domain)
            }
        }

        val status = if (deduplicated) HttpStatus.OK else HttpStatus.CREATED
        return ResponseEntity.status(status).body(document)
    }

    /**
     * Register a document that already exists in MinIO (data-loader path).
     *
     * Called by data-loader after it has uploaded file bytes to the content-addressable
     * MinIO path. Performs dedup check → creates DB record → triggers async ingestion.
     *
     * Returns 200 for deduplicated documents, 201 for new ones.
     */
    @PostMapping("/from-path")
    fun registerFromPath(
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestBody request: FromPathRequest
    ): ResponseEntity<FromPathResponse> {
        val (document, deduplicated) = documentService.registerFromPath(request, tenantId)

        if (!deduplicated) {
            processingScope.launch {
                documentService.processDocument(document.id, tenantId, request.domainHint)
            }
        }

        val status = if (deduplicated) HttpStatus.OK else HttpStatus.CREATED
        return ResponseEntity.status(status).body(FromPathResponse(document = document, deduplicated = deduplicated))
    }

    @GetMapping("/{id}")
    fun getDocument(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestParam("include_chunks", defaultValue = "false") includeChunks: Boolean
    ): ResponseEntity<DocumentDetailResponse> {
        val document = documentService.getDocument(id, tenantId, includeChunks)
            ?: return ResponseEntity.notFound().build()
        return ResponseEntity.ok(document)
    }

    @GetMapping
    fun listDocuments(
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestParam("status", required = false) status: ProcessingStatus?,
        @PageableDefault(size = 20) pageable: Pageable
    ): ResponseEntity<Page<DocumentResponse>> {
        val documents = documentService.listDocuments(tenantId, status, pageable)
        return ResponseEntity.ok(documents)
    }

    @DeleteMapping("/{id}")
    suspend fun deleteDocument(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String
    ): ResponseEntity<*> {
        return try {
            val deleted = documentService.deleteDocument(id, tenantId)
            if (deleted) ResponseEntity.noContent().build<Void>()
            else ResponseEntity.notFound().build<Void>()
        } catch (e: IllegalStateException) {
            logger.warn("Vector deletion failed for document {} (tenant={}): {}", id, tenantId, e.message)
            ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                .body(mapOf("error" to "Vector store unavailable", "detail" to e.message))
        }
    }

    @GetMapping("/{id}/chunks")
    fun getDocumentChunks(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String
    ): ResponseEntity<List<ChunkResponse>> {
        return try {
            val chunks = documentService.getDocumentChunks(id, tenantId)
            ResponseEntity.ok(chunks)
        } catch (e: IllegalArgumentException) {
            ResponseEntity.notFound().build()
        }
    }

    @PostMapping("/{id}/reprocess")
    suspend fun reprocessDocument(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestParam("domain", defaultValue = "auto") domain: String
    ): ResponseEntity<ProcessingResult> {
        val result = documentService.processDocument(id, tenantId, domain)
        return ResponseEntity.ok(result)
    }

    @DeleteMapping("/all")
    suspend fun deleteAllDocuments(
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String
    ): ResponseEntity<Map<String, Any>> {
        val count = documentService.deleteAllDocuments(tenantId)
        return ResponseEntity.ok(mapOf("deleted" to count, "tenant_id" to tenantId))
    }

    // ==========================================================================
    // DataSource endpoints
    // ==========================================================================

    @PostMapping("/data-sources")
    fun createDataSource(
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestBody request: DataSourceRequest
    ): ResponseEntity<DataSourceResponse> {
        val dataSource = documentService.createDataSource(tenantId, request)
        return ResponseEntity.status(HttpStatus.CREATED).body(dataSource)
    }

    @GetMapping("/data-sources/{id}")
    fun getDataSource(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String
    ): ResponseEntity<DataSourceResponse> {
        val dataSource = documentService.getDataSource(id, tenantId)
            ?: return ResponseEntity.notFound().build()
        return ResponseEntity.ok(dataSource)
    }

    @GetMapping("/data-sources")
    fun listDataSources(
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String
    ): ResponseEntity<List<DataSourceResponse>> {
        return ResponseEntity.ok(documentService.listDataSources(tenantId))
    }

    @PostMapping("/data-sources/{id}/complete")
    fun completeDataSource(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestParam("document_count", defaultValue = "0") documentCount: Int
    ): ResponseEntity<DataSourceResponse> {
        return try {
            ResponseEntity.ok(documentService.completeDataSource(id, tenantId, documentCount))
        } catch (e: IllegalArgumentException) {
            ResponseEntity.notFound().build()
        }
    }

    @PostMapping("/data-sources/{id}/fail")
    fun failDataSource(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String
    ): ResponseEntity<DataSourceResponse> {
        return try {
            ResponseEntity.ok(documentService.failDataSource(id, tenantId))
        } catch (e: IllegalArgumentException) {
            ResponseEntity.notFound().build()
        }
    }

    // ==========================================================================
    // Private helpers
    // ==========================================================================

    // ==========================================================================
    // SSE — real-time document status updates
    // ==========================================================================

    /**
     * SSE endpoint: streams document lifecycle events to the UI.
     *
     * On connect:  sends a `current_state` snapshot of all in-flight (PENDING /
     *              PROCESSING) documents so the client can reconcile immediately
     *              without a separate REST call — covering the race between "user
     *              starts upload" and "SSE connection opens".
     *
     * Live events: `document_status` events are pushed at every status transition
     *              (PENDING → PROCESSING → COMPLETED / FAILED).
     *
     * Each event carries an `id:` field and `retry: 3000` so the browser's
     * built-in EventSource reconnect logic works correctly.  The gateway route
     * for this path sets response-timeout=-1 so the connection is not dropped
     * while idle between events.
     */
    @GetMapping("/events", produces = [MediaType.TEXT_EVENT_STREAM_VALUE])
    fun documentStatusEvents(
        @RequestHeader("X-Tenant-Id") tenantId: String,
    ): SseEmitter {
        val emitter = sseRegistry.register(tenantId)

        val snapshot = documentService.listInFlight(tenantId).map { doc ->
            mapOf(
                "documentId"   to doc.id.toString(),
                "status"       to doc.status.name,
                "stage"        to stageLabel(doc.status.name),
                "filename"     to doc.filename,
                "chunkCount"   to doc.chunkCount,
                "errorMessage" to doc.errorMessage,
            )
        }
        sseRegistry.sendSnapshot(emitter, snapshot)

        return emitter
    }

    private fun stageLabel(status: String) = when (status) {
        "PENDING"    -> "Queued"
        "PROCESSING" -> "Processing"
        "COMPLETED"  -> "Indexed"
        "FAILED"     -> "Failed"
        else         -> status
    }

    private fun parseMetadata(metadataJson: String?): Map<String, String> {
        if (metadataJson == null) return emptyMap()
        return runCatching {
            @Suppress("UNCHECKED_CAST")
            objectMapper.readValue(metadataJson, Map::class.java)
                .mapKeys { it.key.toString() }
                .mapValues { it.value.toString() }
        }.getOrDefault(emptyMap())
    }
}
