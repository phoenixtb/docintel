# RAG Service

**Language/Framework:** Python · FastAPI · Haystack 2.x  
**Port:** `8000`  
**Source:** `services/rag-service/`

---

## Responsibilities

- Document indexing: chunking, embedding, and vector storage
- Query processing: semantic search, reranking, LLM generation
- Streaming responses via SSE
- Conversation history persistence (PostgreSQL)
- Semantic response caching (Qdrant)
- Sample dataset loading (HuggingFace)
- Per-tenant LLM model resolution
- Query telemetry emission to analytics-service

---

## Startup Sequence (`lifespan`)

1. Load `Settings` from environment / `.env`
2. Initialize `LangfuseTracer` (if keys present)
3. Pull missing Ollama models (embed + LLM) via Ollama API
4. Ensure Qdrant collections exist (`documents`, `response_cache`)
5. Create `RAGService` instance (pipeline warm-up is lazy)
6. Create `TenantModelResolver` (PostgreSQL-backed, 60s TTL cache per tenant)
7. Create `asyncio.Semaphore(llm_concurrency_limit)` for LLM concurrency control

---

## RAG Pipeline (`pipelines/query.py`)

Built as a Haystack `AsyncPipeline`. Executed via `await pipeline.run_async()`.

```
Query
  │
  ├─ [optional] TransformersZeroShotTextRouter (DeBERTa)
  │    Classifies into: hr_policy | technical | contracts | general
  │    Disabled by default (RAG_USE_DOMAIN_ROUTING=false)
  │
  ├─ [optional] QueryExpander (Ollama qwen3:1.7b)
  │    Generates synonym expansions to bridge vocabulary gap
  │    Disabled by default (USE_QUERY_EXPANSION=false)
  │
  ├─ OllamaTextEmbedder (nomic-embed-text, 768-dim)
  │
  ├─ SemanticCacheChecker (similarity threshold: 0.92)
  │    Hit → return cached answer immediately
  │    Miss → continue to retrieval
  │
  ├─ SecureRetriever (top_k=50)
  │    Qdrant hybrid retriever (dense + BM25 sparse via fastembed)
  │    Applies Qdrant RRF fusion
  │    Mandatory filters: tenant_id, document ACL (allowed_roles, allowed_users)
  │    Optional filter: domain classification
  │
  ├─ SentenceTransformersSimilarityRanker (ms-marco-MiniLM-L-6-v2, top_k=10→5)
  │    Can be disabled per-request (use_reranking=false)
  │
  ├─ PromptBuilder (templates in prompts.py)
  │
  ├─ OllamaChatGenerator (streaming, temperature=0.1, max_tokens=4096)
  │    Model resolved per-tenant from PostgreSQL (60s cache)
  │    Falls back to OLLAMA_LLM_FALLBACK if primary fails
  │
  └─ SemanticCacheWriter (writes miss responses to Qdrant response_cache)
```

**Concurrency:**  
`asyncio.Semaphore(LLM_CONCURRENCY_LIMIT)` gates LLM generation across all tenants.  
- Default: 3 (optimal for single Ollama/single GPU)  
- OpenAI/Anthropic API: set to 20–50+  
- vLLM: set to `gpu_count × 4`

---

## Indexing Pipeline (`pipelines/indexing.py`)

Per-tenant pipeline instance cached in `_indexing_pipeline_cache`.

```
Chunks (text + metadata)
  │
  ├─ OllamaTextEmbedder (dense, 768-dim)
  ├─ FastembedSparseTextEmbedder (BM25, local CPU)
  │
  └─ QdrantDocumentStore.write()
       Collection: documents_{tenant_id}
       Payload: content, tenant_id, document_id, domain,
                allowed_roles, allowed_users, metadata
```

---

## ACL Filtering (`components/retrieval.py`)

`SecureRetriever` builds a mandatory Qdrant filter combining:
- `tenant_id = <current_tenant>` (always enforced)
- `OR domain IN [classified_domain, "general"]` (if domain routing enabled)
- ACL `should` block (nested in `must` to guarantee enforcement):
  - `allowed_roles IS EMPTY OR NULL` (no restriction = public)
  - OR `allowed_roles CONTAINS <user_role>`
  - OR `allowed_users CONTAINS <user_id>`

---

## Semantic Cache (`components/cache.py`)

Uses Qdrant `response_cache` collection.

- **Check:** Embeds query → searches for similarity > `RAG_CACHE_SIMILARITY_THRESHOLD` (0.92)
- **Write:** Stores query embedding + full response after generation
- **Invalidate:** Deletes all cache points for a `tenant_id` (called after new indexing)

---

## Model Resolution (`components/model_resolver.py`)

`TenantModelResolver` resolves effective LLM per request:

1. Query `platform_settings` table for global override
2. If platform override is set → use it for all tenants
3. Else query `tenants.settings->>'llm_model'` for tenant preference
4. Else fall back to `OLLAMA_LLM_MODEL` env var

Resolution result is cached per tenant for 60 seconds using `asyncio.get_running_loop()`.

---

## Conversation Persistence (`db.py`)

PostgreSQL-backed via SQLAlchemy (sync). All DB calls run in `ThreadPoolExecutor` via `_run_db()` helper which copies `ContextVars` for RLS propagation.

Row-Level Security enforced via:
- `app.current_tenant` PostgreSQL session variable
- `app.user_role` PostgreSQL session variable

Tables used: `conversations`, `messages`

---

## API Endpoints

All endpoints receive `X-Tenant-Id`, `X-User-Id`, `X-User-Role` headers from the gateway.

### Query

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/query/stream` | JWT | Streaming RAG query (SSE). Includes semantic cache check, retrieval, reranking, LLM generation |
| `POST` | `/query` | JWT | Synchronous RAG query (non-streaming, same pipeline) |

**Request body (`/query/stream`):**
```json
{
  "question": "What is the vacation policy?",
  "conversation_id": "uuid",
  "use_cache": true,
  "use_reranking": true
}
```

**SSE stream format:**
```
data: {"type": "token", "content": "The vacation"}
data: {"type": "token", "content": " policy states..."}
data: {"type": "sources", "sources": [...]}
data: {"type": "done"}
data: {"type": "error", "detail": "..."}
```

### Indexing

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/index` | JWT | Index document chunks into Qdrant |
| `DELETE` | `/index/{tenant_id}/{document_id}` | JWT (`tenant_admin`+) | Delete vectors for a document |
| `DELETE` | `/index/{tenant_id}` | JWT (`tenant_admin`+) | Delete all vectors for a tenant |

### Conversations

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/conversations` | JWT | Create conversation |
| `GET` | `/conversations` | JWT | List conversations for user |
| `GET` | `/conversations/{id}` | JWT | Get conversation with messages |
| `PATCH` | `/conversations/{id}` | JWT | Update title |
| `DELETE` | `/conversations/{id}` | JWT | Delete conversation |

### Models

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/models` | JWT | List available Ollama models |

### Datasets

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/sample-datasets` | JWT | List available sample datasets |
| `POST` | `/sample-datasets/load` | JWT (`tenant_admin`+) | Load HuggingFace datasets into Qdrant |

### Misc

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | Public | Health check |
| `GET` | `/vector-stats` | JWT | Qdrant collection counts per tenant |
| `POST` | `/classify-domain` | JWT | Classify text into domain (used by doc-service) |

---

## Qdrant Collections

| Collection | Purpose | Vector | Payload fields |
|---|---|---|---|
| `documents` | Document chunks per tenant | 768-dim dense + sparse | `content`, `tenant_id`, `document_id`, `domain`, `allowed_roles`, `allowed_users`, `metadata` |
| `response_cache` | Cached RAG responses | 768-dim dense | `query`, `response`, `sources`, `tenant_id`, `created_at` |

Payload indexes created on: `tenant_id`, `document_id`, `domain`, `allowed_roles`, `allowed_users`.

---

## Key Files

| File | Purpose |
|------|---------|
| `api/main.py` | FastAPI app, all endpoints, lifespan, streaming generator |
| `api/dependencies.py` | FastAPI `Depends()`: JWT claims, settings, RAGService, tracer |
| `config.py` | All env var settings via Pydantic `BaseSettings` |
| `context.py` | `ContextVar` definitions for `_tenant_ctx`, `_role_ctx` (RLS) |
| `db.py` | SQLAlchemy session factory, conversation/message CRUD |
| `schemas.py` | Pydantic output schemas (`RAGStructuredResponse`) |
| `prompts.py` | Prompt templates and fallback response strings |
| `chunking.py` | `ChunkingService`: sentence, semantic, recursive strategies via Chonkie |
| `datasets.py` | HuggingFace dataset loaders (TechQA, HR Policies, CUAD Contracts) |
| `tracing.py` | Langfuse tracer wrapper |
| `stores.py` | `QdrantDocumentStore` factory (per-tenant) |
| `components/cache.py` | `SemanticCacheChecker`, `SemanticCacheWriter`, `invalidate_cache` |
| `components/embedders.py` | Ollama dense embedder, Fastembed sparse embedder factories |
| `components/retrieval.py` | `SecureRetriever`, `_build_acl_filter`, `_retriever_cache` |
| `components/routing.py` | `DomainRouter` (zero-shot DeBERTa) |
| `components/query_transform.py` | `QueryExpander` (Ollama-based) |
| `components/prompt.py` | Haystack `PromptBuilder` wrapper |
| `components/generation.py` | `OllamaChatGenerator` wrapper |
| `components/model_resolver.py` | `TenantModelResolver` with 60s TTL cache |
| `components/observability.py` | Langfuse span helpers |
| `pipelines/query.py` | `RAGService` class, pipeline construction, `warm_up()` |
| `pipelines/indexing.py` | `index_chunks()`, `_indexing_pipeline_cache` |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint |
| `QDRANT_COLLECTION` | `documents` | Base collection name |
| `QDRANT_CACHE_COLLECTION` | `response_cache` | Cache collection name |
| `QDRANT_EMBEDDING_DIM` | `768` | Must match embed model output |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama API |
| `OLLAMA_LLM_MODEL` | `qwen3.5:4b` | Primary chat model |
| `OLLAMA_LLM_FALLBACK` | `phi3:mini` | Fallback chat model |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | Dense embedding model |
| `OLLAMA_LLM_TEMPERATURE` | `0.1` | Generation temperature |
| `OLLAMA_LLM_MAX_TOKENS` | `4096` | Max output tokens |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder reranker |
| `USE_RERANKING` | `true` | Enable/disable reranking |
| `RAG_RETRIEVER_TOP_K` | `50` | Initial retrieval count |
| `RAG_RERANKER_TOP_K` | `10` | Reranker input count |
| `RAG_DEFAULT_TOP_K` | `5` | Final context documents |
| `RAG_CACHE_SIMILARITY_THRESHOLD` | `0.92` | Cache hit threshold |
| `RAG_USE_HYBRID_SEARCH` | `true` | Dense + sparse (BM25) fusion |
| `LLM_CONCURRENCY_LIMIT` | `3` | Max concurrent LLM requests |
| `USE_CACHE` | `true` | Enable semantic caching |
| `RAG_USE_DOMAIN_ROUTING` | `false` | Enable DeBERTa domain classifier |
| `USE_QUERY_EXPANSION` | `false` | Enable Ollama-based query expansion |
| `POSTGRES_URL` | `postgresql://...` | Conversation history DB |
| `LANGFUSE_PUBLIC_KEY` | `` | Langfuse tracing (optional) |
| `LANGFUSE_SECRET_KEY` | `` | Langfuse tracing (optional) |
| `ANALYTICS_SERVICE_URL` | `http://analytics-service:8001` | Telemetry endpoint |
