package com.docintel.document.controller

import com.docintel.document.dto.*
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.service.DocumentService
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
import java.util.UUID

@RestController
@RequestMapping("/internal/documents")
class DocumentController(
    private val documentService: DocumentService,
    private val objectMapper: ObjectMapper,
) {
    private val logger = LoggerFactory.getLogger(DocumentController::class.java)
    private val processingScope = CoroutineScope(
        SupervisorJob() + Dispatchers.Default +
        CoroutineExceptionHandler { _, t -> logger.error("Background processing error", t) }
    )

    /**
     * Upload a new document.
     * The document is stored and processing is triggered asynchronously.
     * 
     * @param domain: Optional domain classification.
     *   - "auto" or empty: Auto-detect using zero-shot classification
     *   - "technical", "hr_policy", "contracts", "general": Manual override
     */
    @PostMapping(consumes = [MediaType.MULTIPART_FORM_DATA_VALUE])
    fun uploadDocument(
        @RequestParam("file") file: MultipartFile,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestParam("domain", defaultValue = "auto") domain: String,
        @RequestParam("metadata", required = false) metadataJson: String?,
        @RequestHeader("Idempotency-Key", required = false) idempotencyKey: String?
    ): ResponseEntity<DocumentResponse> {
        // If an idempotency key is supplied, surface it to metadata so callers
        // can detect and de-duplicate concurrent retries on the application level.
        // Full server-side idempotency (check-then-insert) requires a dedicated
        // idempotency_keys table which should be added in a future migration.
        
        val metadata = if (metadataJson != null) {
            runCatching {
                @Suppress("UNCHECKED_CAST")
                objectMapper.readValue(metadataJson, Map::class.java)
                    .mapKeys { it.key.toString() }
                    .mapValues { it.value.toString() }
                    .toMutableMap()
            }.getOrDefault(mutableMapOf())
        } else {
            mutableMapOf()
        }

        if (domain.isNotBlank() && domain != "auto") {
            metadata["domain_hint"] = domain
        }
        idempotencyKey?.let { metadata["idempotency_key"] = it }

        val document = documentService.uploadDocument(file, tenantId, metadata)

        // Trigger async processing with domain parameter
        processingScope.launch {
            documentService.processDocument(document.id, tenantId, domain)
        }

        return ResponseEntity
            .status(HttpStatus.CREATED)
            .body(document)
    }

    /**
     * Get a document by ID.
     */
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

    /**
     * List documents for a tenant.
     */
    @GetMapping
    fun listDocuments(
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestParam("status", required = false) status: ProcessingStatus?,
        @PageableDefault(size = 20) pageable: Pageable
    ): ResponseEntity<Page<DocumentResponse>> {
        val documents = documentService.listDocuments(tenantId, status, pageable)
        return ResponseEntity.ok(documents)
    }

    /**
     * Delete a document.
     */
    @DeleteMapping("/{id}")
    suspend fun deleteDocument(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String
    ): ResponseEntity<Void> {
        val deleted = documentService.deleteDocument(id, tenantId)
        
        return if (deleted) {
            ResponseEntity.noContent().build()
        } else {
            ResponseEntity.notFound().build()
        }
    }

    /**
     * Get chunks for a document.
     */
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

    /**
     * Reprocess a document with different settings.
     */
    @PostMapping("/{id}/reprocess")
    suspend fun reprocessDocument(
        @PathVariable id: UUID,
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String,
        @RequestParam("domain", defaultValue = "auto") domain: String
    ): ResponseEntity<ProcessingResult> {
        val result = documentService.processDocument(id, tenantId, domain)
        return ResponseEntity.ok(result)
    }

    /**
     * Bulk create document records (for sample datasets).
     * Creates document records in PostgreSQL without file upload.
     * Used by RAG service when loading sample datasets to sync with document list.
     */
    @PostMapping("/bulk-create")
    fun bulkCreateDocuments(
        @RequestHeader("X-Tenant-Id", required = false) tenantIdHeader: String?,
        @RequestBody request: BulkDocumentCreateRequest
    ): ResponseEntity<BulkDocumentCreateResponse> {
        // Header is authoritative (set by gateway or trusted internal callers);
        // fall back to body for backwards compat.
        val effectiveTenantId = tenantIdHeader?.takeIf { it.isNotBlank() } ?: request.tenantId
        val response = documentService.bulkCreateDocuments(request.copy(tenantId = effectiveTenantId))
        return ResponseEntity
            .status(HttpStatus.CREATED)
            .body(response)
    }

    /**
     * Delete all documents for a tenant.
     * Removes documents, chunks, vectors, and files.
     */
    @DeleteMapping("/all")
    suspend fun deleteAllDocuments(
        @RequestHeader("X-Tenant-Id", defaultValue = "default") tenantId: String
    ): ResponseEntity<Map<String, Any>> {
        val count = documentService.deleteAllDocuments(tenantId)
        return ResponseEntity.ok(mapOf("deleted" to count, "tenant_id" to tenantId))
    }
}
