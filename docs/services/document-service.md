# Document Service

**Language/Framework:** Kotlin · Spring Boot 3.x · JDBC (`JdbcTemplate`) · Coroutines  
**Port:** `8081`  
**Source:** `services/document-service/`

---

## Responsibilities

- Accept file uploads (PDF, DOCX, TXT) via multipart form
- Store raw files in the S3-compatible object store ([contract](../contracts/object-storage.md))
- Extract text content using Apache Tika
- Persist document metadata in PostgreSQL
- Trigger RAG service indexing (async, per-document)
- Serve document listing, details, and deletion
- Expose a `bulk-create` endpoint for sample dataset sync
- Support reprocessing with different domain hints

---

## Multi-Tenancy Pattern

Same RLS pattern as admin-service:

- `TenantContextFilter` reads `X-Tenant-Id` / `X-User-Role` from gateway headers → `TenantContextHolder`
- `TenantAwareDataSource` sets `app.current_tenant` and `app.user_role` on every JDBC connection
- All SQL operates on rows visible under the current tenant's RLS policy

Connects as `docintel_app` (non-superuser) so PostgreSQL RLS is always enforced.

---

## Upload Flow

1. `POST /internal/documents` (multipart)
2. `DocumentService.uploadDocument()`:
   - Save file to the object store (bucket `docintel-{tenantId}`, key `docs/{contentHash}/original.{ext}`)
   - Create `documents` row with `status = PENDING`
3. Async `CoroutineScope(Dispatchers.Default).launch { processDocument(...) }`:
   - Extract text via `TextExtractionService` (Apache Tika)
   - Call `RagServiceClient.indexChunks()` → RAG service `/index`
   - Update document `status = COMPLETED` and `chunk_count`
   - On failure: set `status = FAILED`, store `error_message`

---

## Document Status Lifecycle

```
PENDING → (text extraction + embedding) → COMPLETED
        → (on any error)              → FAILED
```

Reprocessing (`POST /{id}/reprocess`) resets status and re-runs the pipeline.

---

## API Endpoints

All routes are under `/internal/documents/`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/internal/documents` | Upload document (multipart); async processing |
| `GET` | `/internal/documents` | List documents for tenant (paginated, filter by status) |
| `GET` | `/internal/documents/{id}` | Get document by ID; optional `include_chunks=true` |
| `DELETE` | `/internal/documents/{id}` | Mark DELETING and queue a deletion task (202); `DeletionTaskWorker` removes vectors, object-store files and rows |
| `GET` | `/internal/documents/{id}/chunks` | List chunks for a document |
| `POST` | `/internal/documents/{id}/reprocess` | Re-extract and re-index with optional domain override |
| `POST` | `/internal/documents/bulk-create` | Create document records without file (used by RAG sample datasets) |
| `DELETE` | `/internal/documents/all` | Delete all documents for tenant |

### Upload Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file` | multipart | required | Document file |
| `domain` | string | `auto` | Domain hint: `auto`, `technical`, `hr_policy`, `contracts`, `general` |
| `metadata` | JSON string | `{}` | Arbitrary key-value metadata |

When `domain = auto`, the RAG service's `/classify-domain` endpoint classifies the document. When explicitly set, the domain is stored as `domain_hint` in metadata and forwarded to the indexing pipeline.

---

## Text Extraction (`TextExtractionService`)

Uses Apache Tika to extract text from:
- PDF
- DOCX / DOC
- TXT / plain text
- Other formats Tika supports

Extracted text is passed to `RagServiceClient.indexChunks()` as raw content.

---

## RAG Service Integration (`RagServiceClient`)

HTTP client (`RestTemplate` with timeouts) to the RAG service.

- `indexChunks(tenantId, documentId, content, domain, metadata)`: `POST /index`
- Returns chunk count, which is stored back on the `documents` row.

---

## Storage (`StorageService`)

S3 operations (AWS SDK v2 `S3Client`, see [object-storage contract](../contracts/object-storage.md)):
- **Upload**: `PUT docintel-{tenantId}/docs/{contentHash}/original.{ext}` — content-addressable, so idempotent
- **Delete document**: list `docs/{contentHash}/` and `DeleteObjects` in batches of 1000 (called by `DeletionTaskWorker`); a missing bucket counts as already deleted

The tenant bucket is created on first upload (HEAD, then CREATE; "already owned" is success).

---

## Bulk Create (`/bulk-create`)

Used by the RAG service when loading HuggingFace sample datasets to keep the PostgreSQL `documents` table in sync with vectors stored in Qdrant. Creates document records without physical file upload.

The `X-Tenant-Id` header takes precedence over `tenantId` in the request body.

---

## Key Files

| File | Purpose |
|------|---------|
| `controller/DocumentController.kt` | REST endpoints, async processing trigger |
| `service/DocumentService.kt` | Business logic: upload, process, delete, list |
| `service/TextExtractionService.kt` | Apache Tika text extraction |
| `service/StorageService.kt` | Object-store (S3) file operations |
| `service/RagServiceClient.kt` | HTTP client to RAG service `/index` |
| `dto/DocumentDto.kt` | Request/response DTOs |
| `entity/Document.kt` | Document entity (id, tenant_id, filename, status, chunk_count, …) |
| `entity/Chunk.kt` | Chunk entity (id, document_id, content, chunk_index, …) |
| `repository/DocumentRepository.kt` | JDBC document queries |
| `repository/ChunkRepository.kt` | JDBC chunk queries |
| `tenant/TenantContextFilter.kt` | Header-to-context mapping |
| `tenant/TenantAwareDataSource.kt` | PostgreSQL RLS session variable injection |
| `config/ObjectStoreConfig.kt` / `ObjectStoreProperties.kt` | S3 client bean; validated `object-store.*` settings |
| `config/TenantDataSourceConfig.kt` | HikariCP pool wrapped with TenantAwareDataSource |

---

## Database Tables Used

| Table | Operations |
|-------|-----------|
| `documents` | Full CRUD; RLS by `tenant_id` |
| `chunks` | INSERT on indexing, DELETE on document delete; RLS by `tenant_id` |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SPRING_DATASOURCE_URL` | `jdbc:postgresql://postgres:5432/docintel?user=docintel_app&password=docintel_app_secret` | PostgreSQL (RLS enforced) |
| `OBJECT_STORE_ENDPOINT` | `http://object-store:7070` | S3 endpoint (any S3-compatible server) |
| `OBJECT_STORE_REGION` | `us-east-1` | Signing region |
| `OBJECT_STORE_ACCESS_KEY` | — (required) | S3 credentials |
| `OBJECT_STORE_SECRET_KEY` | — (required) | S3 credentials |
| `OBJECT_STORE_FORCE_PATH_STYLE` | `true` | Path-style addressing |
| `RAG_SERVICE_URL` | `http://rag-service:8000` | RAG service for indexing calls |
