# DocIntel Technical Documentation

## Overview

DocIntel is an enterprise-grade document intelligence platform that enables semantic search and AI-powered Q&A over organizational documents. It features domain-aware routing, multi-tenant isolation, and a modern microservices architecture.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         Web UI (SvelteKit)                           │   │
│  │  • Chat Interface (SSE streaming)                                    │   │
│  │  • Document Management (upload, list, delete)                        │   │
│  │  • Sample Dataset Loading                                            │   │
│  │  • Dark/Light/System Theme                                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼ HTTP :8080                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                              GATEWAY LAYER                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    API Gateway (Spring WebFlux)                      │   │
│  │  • Request Routing                                                   │   │
│  │  • Rate Limiting (Token Bucket)                                      │   │
│  │  • Tenant Injection                                                  │   │
│  │  • CORS Configuration                                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                          │                    │                             │
│              ┌───────────┴─────────┐         │                             │
│              ▼                     ▼         ▼                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                             SERVICE LAYER                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          │
│  │ Document Service │  │   RAG Service    │  │  Admin Service   │          │
│  │ (Spring Boot)    │  │   (FastAPI)      │  │  (Spring Boot)   │          │
│  │                  │  │                  │  │                  │          │
│  │ • File Upload    │  │ • Query Pipeline │  │ • Cache Mgmt     │          │
│  │ • Text Extract   │  │ • Indexing       │  │ • Health Checks  │          │
│  │ • Chunk Storage  │  │ • Semantic Cache │  │ • Statistics     │          │
│  │ • Status Track   │  │ • Domain Routing │  │                  │          │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘          │
│           │                     │                     │                     │
├───────────┼─────────────────────┼─────────────────────┼─────────────────────┤
│           ▼                     ▼                     ▼                     │
│                           DATA LAYER                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  PostgreSQL  │  │    MinIO     │  │   Qdrant     │  │    Redis     │    │
│  │              │  │              │  │              │  │              │    │
│  │ • Documents  │  │ • Raw Files  │  │ • Vectors    │  │ • Rate Limit │    │
│  │ • Chunks     │  │ • PDFs       │  │ • Embeddings │  │ • Sessions   │    │
│  │ • Metadata   │  │ • Exports    │  │ • Cache      │  │              │    │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘    │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                           AI/ML LAYER                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         Ollama (Local LLM)                            │  │
│  │  • qwen3:4b (Primary) - Question Answering                           │  │
│  │  • phi3:mini (Fallback) - Lightweight Alternative                    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                    Embedding & Reranking Models                       │  │
│  │  • nomic-embed-text-v1.5 (768-dim embeddings)                        │  │
│  │  • cross-encoder/ms-marco-MiniLM-L-6-v2 (reranking)                  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                        OBSERVABILITY LAYER                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         Langfuse (Tracing)                            │  │
│  │  • LLM Call Traces                                                    │  │
│  │  • Latency Metrics                                                    │  │
│  │  • Token Usage                                                        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Frontend** | SvelteKit 2.x, Tailwind CSS, TypeScript | Reactive UI with SSE streaming |
| **API Gateway** | Spring WebFlux (Kotlin), Netty | Non-blocking routing, rate limiting |
| **Document Service** | Spring Boot (Kotlin), R2DBC | File handling, text extraction |
| **RAG Service** | FastAPI (Python), Haystack 2.x | Embeddings, retrieval, generation |
| **Admin Service** | Spring Boot (Kotlin) | Cache management, health monitoring |
| **Vector Store** | Qdrant | Semantic search, response caching |
| **Database** | PostgreSQL 15 | Document metadata, chunk storage |
| **Object Storage** | MinIO | Raw file storage (S3-compatible) |
| **Cache** | Redis | Rate limiting, session storage |
| **LLM** | Ollama (Qwen3-4B, Phi3-mini) | Local inference, no API costs |
| **Embeddings** | nomic-embed-text-v1.5 | 768-dimensional vectors |
| **Reranking** | ms-marco-MiniLM-L-6-v2 | Cross-encoder reranking |
| **Tracing** | Langfuse | LLM observability |

---

## Component Details

### 1. Web UI (`services/web-ui`)

**Purpose**: User-facing SvelteKit application for chat and document management.

| File | Purpose |
|------|---------|
| `src/routes/+page.svelte` | Chat interface with SSE streaming |
| `src/routes/documents/+page.svelte` | Document upload, sample data loading |
| `src/lib/components/ThemeSwitcher.svelte` | Dark/Light/System theme toggle |
| `src/app.css` | Global styles with Tailwind |
| `tailwind.config.js` | Tailwind configuration with dark mode |
| `svelte.config.js` | SvelteKit configuration |
| `playwright.config.ts` | E2E test configuration |

### 2. API Gateway (`services/api-gateway`)

**Purpose**: Entry point for all API requests. Routes to downstream services.

| File | Purpose |
|------|---------|
| `ApiGatewayApplication.kt` | Spring Boot entry point |
| `config/GatewayConfig.kt` | Route definitions to services |
| `config/SecurityConfig.kt` | CORS, security headers |
| `filter/RateLimitFilter.kt` | Token bucket rate limiting |
| `filter/TenantFilter.kt` | Tenant ID injection from headers |
| `controller/HealthController.kt` | Gateway health endpoint |

**Routes**:
```
/api/v1/documents/**  → document-service:8081
/api/v1/query/**      → rag-service:8000
/api/v1/admin/**      → admin-service:8082
/api/v1/sample-datasets/** → rag-service:8000
/api/v1/vector-stats  → rag-service:8000
```

### 3. Document Service (`services/document-service`)

**Purpose**: Handles file uploads, text extraction, and triggers indexing.

| File | Purpose |
|------|---------|
| `DocumentServiceApplication.kt` | Spring Boot entry point |
| `controller/DocumentController.kt` | REST endpoints for CRUD |
| `service/DocumentService.kt` | Business logic, orchestration |
| `service/StorageService.kt` | MinIO file operations |
| `service/TextExtractionService.kt` | PDF/DOCX text extraction |
| `service/RagServiceClient.kt` | HTTP client to RAG service |
| `entity/Document.kt` | JPA entity for documents |
| `entity/Chunk.kt` | JPA entity for chunks |
| `repository/DocumentRepository.kt` | R2DBC repository |
| `config/MinioConfig.kt` | MinIO client configuration |

### 4. RAG Service (`services/rag-service`)

**Purpose**: Core AI/ML service for embeddings, retrieval, and generation.

| File | Purpose |
|------|---------|
| `api/main.py` | FastAPI app with all endpoints |
| `prompts.py` | Centralized prompt templates |
| `chunking.py` | Document chunking strategies |
| `datasets.py` | HuggingFace dataset loading |
| `tracing.py` | Langfuse integration |
| **components/** | |
| `cache.py` | Semantic cache (check/write/invalidate) |
| `llm.py` | LiteLLM wrapper with fallbacks |
| `prompt.py` | Haystack prompt builder |
| `retriever.py` | Secure retriever with ACL filtering |
| `router.py` | Domain classification |
| **pipelines/** | |
| `indexing.py` | Embedding + Qdrant storage |
| `query.py` | Full RAG query pipeline |

### 5. Admin Service (`services/admin-service`)

**Purpose**: Administrative operations and system monitoring.

| File | Purpose |
|------|---------|
| `AdminServiceApplication.kt` | Spring Boot entry point |
| `controller/AdminController.kt` | Admin REST endpoints |
| `service/CacheService.kt` | Redis/Qdrant cache management |
| `service/HealthService.kt` | Dependency health checks |
| `service/StatsService.kt` | System statistics |
| `dto/AdminDto.kt` | Request/response DTOs |

---

## Data Flows

### Flow 1: Document Upload

```
┌──────┐     ┌─────────┐     ┌─────────────┐     ┌───────┐     ┌────────┐
│ User │────▶│ Web UI  │────▶│ API Gateway │────▶│ Doc   │────▶│ MinIO  │
└──────┘     └─────────┘     └─────────────┘     │Service│     │(file)  │
                                                  │       │     └────────┘
                                                  │       │
                                                  │       │────▶┌────────────┐
                                                  │       │     │ PostgreSQL │
                                                  │       │     │ (metadata) │
                                                  │       │     └────────────┘
                                                  │       │
                                                  │       │────▶┌─────────────┐
                                                  │       │     │ RAG Service │
                                                  └───────┘     │ (chunking)  │
                                                                │             │
                                                                │      ▼      │
                                                                │ ┌─────────┐ │
                                                                │ │ Qdrant  │ │
                                                                │ │(vectors)│ │
                                                                └─┴─────────┴─┘
```

**Steps**:
1. User uploads file via Web UI
2. API Gateway routes to Document Service
3. Document Service saves file to MinIO
4. Document Service creates record in PostgreSQL (status: PENDING)
5. Document Service extracts text (PDF/DOCX support)
6. Document Service calls RAG Service `/index` endpoint
7. RAG Service chunks the text
8. RAG Service generates embeddings (nomic-embed-text)
9. RAG Service stores chunks + vectors in Qdrant
10. RAG Service invalidates response cache for tenant
11. Document Service updates status to COMPLETED

### Flow 2: Sample Dataset Loading

```
┌──────┐     ┌─────────┐     ┌─────────────┐     ┌─────────────┐
│ User │────▶│ Web UI  │────▶│ API Gateway │────▶│ RAG Service │
└──────┘     └─────────┘     └─────────────┘     │             │
                                                  │      │      │
                                                  │      ▼      │
                                                  │ ┌──────────┐│
                                                  │ │HuggingFace│
                                                  │ │ Datasets ││
                                                  │ └──────────┘│
                                                  │      │      │
                                                  │      ▼      │
                                                  │ ┌─────────┐ │
                                                  │ │ Chunking│ │
                                                  │ └─────────┘ │
                                                  │      │      │
                                                  │      ▼      │
                                                  │ ┌─────────┐ │
                                                  │ │ Qdrant  │ │
                                                  └─┴─────────┴─┘
```

**Steps**:
1. User selects datasets (TechQA, HR Policies, CUAD Contracts)
2. RAG Service downloads from HuggingFace
3. Documents are chunked (semantic/sentence splitting)
4. Chunks are embedded and stored in Qdrant
5. Response cache is invalidated
6. UI shows indexed chunk counts

**Note**: Sample datasets bypass PostgreSQL/MinIO (no document records created).

### Flow 3: Query with Streaming Response

```
┌──────┐     ┌─────────┐     ┌─────────────┐     ┌─────────────┐
│ User │────▶│ Web UI  │────▶│ API Gateway │────▶│ RAG Service │
│      │     │  (SSE)  │     │             │     │             │
└──────┘     └─────────┘     └─────────────┘     │             │
                  ▲                               │      │      │
                  │                               │      ▼      │
                  │                               │ ┌─────────┐ │
                  │ tokens                        │ │Embed    │ │
                  │ streaming                     │ │Query    │ │
                  │                               │ └────┬────┘ │
                  │                               │      │      │
                  │                               │      ▼      │
                  │                               │ ┌─────────┐ │
                  │                               │ │Cache    │◀┼─ Hit? Return cached
                  │                               │ │Check    │ │
                  │                               │ └────┬────┘ │
                  │                               │      │      │
                  │                               │      ▼ Miss │
                  │                               │ ┌─────────┐ │
                  │                               │ │Qdrant   │ │
                  │                               │ │Retrieve │ │
                  │                               │ └────┬────┘ │
                  │                               │      │      │
                  │                               │      ▼      │
                  │                               │ ┌─────────┐ │
                  │                               │ │Rerank   │ │
                  │                               │ │(top 5)  │ │
                  │                               │ └────┬────┘ │
                  │                               │      │      │
                  │                               │      ▼      │
                  │◀──────────────────────────────┼ ┌─────────┐ │
                  │         SSE stream            │ │ Ollama  │ │
                  │                               │ │ (LLM)   │ │
                  └───────────────────────────────┴─┴─────────┴─┘
```

**RAG Pipeline Steps**:
1. Embed query using nomic-embed-text-v1.5
2. Check semantic cache (similarity > 0.92 = cache hit)
3. If miss: retrieve from Qdrant with tenant/ACL filters
4. Rerank top 50 → top 5 using cross-encoder
5. Build prompt with context documents
6. Stream tokens from Ollama (Qwen3-4B)
7. Return sources with response

### Flow 4: Cache Invalidation

```
┌───────────────┐     ┌─────────────┐
│ Index Chunks  │────▶│ Qdrant      │
│ (new docs)    │     │ documents   │
└───────────────┘     └─────────────┘
        │
        ▼
┌───────────────┐     ┌─────────────┐
│ Invalidate    │────▶│ Qdrant      │
│ Cache         │     │ response_   │
│ (tenant_id)   │     │ cache       │
└───────────────┘     └─────────────┘
                            │
                            ▼
                      DELETE all points
                      WHERE tenant_id = X
```

---

## Qdrant Collections

| Collection | Purpose | Vector Dim | Stored Data |
|------------|---------|------------|-------------|
| `documents` | Document chunks | 768 | content, tenant_id, document_id, domain, metadata |
| `response_cache` | Cached responses | 768 | query, response, sources, tenant_id, created_at |

---

## Project Structure

```
docintel/
├── .env.example                    # Environment variables template
├── .gitignore                      # Git ignore rules
├── docker-compose.yml              # Main compose file
├── docker-compose.override.yml     # Local development overrides
├── README.md                       # Quick start guide
│
├── .github/workflows/              # CI/CD pipelines
│   ├── ci.yml                      # Main CI workflow
│   ├── document-service.yml        # Document service checks
│   └── rag-service.yml             # RAG service checks
│
├── config/                         # Infrastructure configs
│   ├── langfuse/init.sh            # Langfuse initialization
│   ├── postgres/init.sql           # Database schema
│   └── qdrant/init-collections.sh  # Qdrant setup
│
├── docs/                           # Documentation
│   ├── TECHNICAL.md                # This file
│   ├── api-docs.md                 # API reference
│   ├── architecture.md             # Architecture overview
│   └── part2b-project-spec.md      # Original spec
│
├── notebooks/
│   └── evaluation_demo.ipynb       # RAG evaluation notebook
│
├── scripts/
│   ├── setup.sh                    # First-time setup (Ollama, deps)
│   ├── start-app.sh                # Daily startup script
│   ├── seed-data.sh                # Load sample data via CLI
│   ├── evaluate.py                 # Evaluation metrics script
│   └── cleanup.sh                  # Clean Docker resources
│
├── services/
│   ├── admin-service/              # Kotlin/Spring Boot
│   │   ├── Dockerfile
│   │   ├── build.gradle.kts
│   │   └── src/
│   │       ├── main/kotlin/com/docintel/admin/
│   │       │   ├── AdminServiceApplication.kt
│   │       │   ├── controller/AdminController.kt
│   │       │   ├── dto/AdminDto.kt
│   │       │   └── service/
│   │       │       ├── CacheService.kt
│   │       │       ├── HealthService.kt
│   │       │       └── StatsService.kt
│   │       └── test/kotlin/...
│   │
│   ├── api-gateway/                # Kotlin/Spring WebFlux
│   │   ├── Dockerfile
│   │   ├── build.gradle.kts
│   │   └── src/
│   │       ├── main/kotlin/com/docintel/gateway/
│   │       │   ├── ApiGatewayApplication.kt
│   │       │   ├── config/
│   │       │   │   ├── GatewayConfig.kt
│   │       │   │   └── SecurityConfig.kt
│   │       │   ├── controller/HealthController.kt
│   │       │   └── filter/
│   │       │       ├── RateLimitFilter.kt
│   │       │       └── TenantFilter.kt
│   │       └── test/kotlin/...
│   │
│   ├── document-service/           # Kotlin/Spring Boot
│   │   ├── Dockerfile
│   │   ├── build.gradle.kts
│   │   └── src/
│   │       ├── main/kotlin/com/docintel/document/
│   │       │   ├── DocumentServiceApplication.kt
│   │       │   ├── config/MinioConfig.kt
│   │       │   ├── controller/DocumentController.kt
│   │       │   ├── dto/DocumentDto.kt
│   │       │   ├── entity/
│   │       │   │   ├── Chunk.kt
│   │       │   │   └── Document.kt
│   │       │   ├── repository/
│   │       │   │   ├── ChunkRepository.kt
│   │       │   │   └── DocumentRepository.kt
│   │       │   └── service/
│   │       │       ├── DocumentService.kt
│   │       │       ├── RagServiceClient.kt
│   │       │       ├── StorageService.kt
│   │       │       └── TextExtractionService.kt
│   │       └── test/kotlin/...
│   │
│   ├── rag-service/                # Python/FastAPI
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── pytest.ini
│   │   └── src/
│   │       ├── __init__.py
│   │       ├── api/
│   │       │   ├── __init__.py
│   │       │   └── main.py         # FastAPI app, all endpoints
│   │       ├── chunking.py         # Chunking strategies
│   │       ├── datasets.py         # HuggingFace loaders
│   │       ├── prompts.py          # Centralized prompts
│   │       ├── tracing.py          # Langfuse integration
│   │       ├── components/
│   │       │   ├── __init__.py
│   │       │   ├── cache.py        # Semantic cache
│   │       │   ├── llm.py          # LiteLLM generator
│   │       │   ├── prompt.py       # Prompt builder
│   │       │   ├── retriever.py    # Secure retriever
│   │       │   └── router.py       # Domain router
│   │       └── pipelines/
│   │           ├── __init__.py
│   │           ├── indexing.py     # Index pipeline
│   │           └── query.py        # Query pipeline
│   │
│   └── web-ui/                     # SvelteKit/TypeScript
│       ├── Dockerfile
│       ├── package.json
│       ├── tailwind.config.js
│       ├── svelte.config.js
│       ├── playwright.config.ts
│       └── src/
│           ├── app.css             # Global styles
│           ├── app.html            # HTML template
│           ├── lib/components/
│           │   └── ThemeSwitcher.svelte
│           └── routes/
│               ├── +layout.svelte  # Root layout
│               ├── +page.svelte    # Chat page
│               └── documents/
│                   └── +page.svelte # Documents page
│
└── tests/
    ├── e2e/.gitkeep               # End-to-end tests
    └── integration/.gitkeep       # Integration tests
```

---

## API Endpoints

### RAG Service (`:8000`)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| GET | `/vector-stats` | Qdrant vector counts |
| POST | `/query` | Synchronous query |
| POST | `/query/stream` | Streaming query (SSE) |
| POST | `/index` | Index document chunks |
| DELETE | `/index/{tenant_id}/{document_id}` | Delete document vectors |
| GET | `/sample-datasets` | List available datasets |
| POST | `/sample-datasets/load` | Load sample datasets |

### Document Service (`:8081`)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| GET | `/documents` | List documents |
| GET | `/documents/{id}` | Get document details |
| POST | `/documents` | Upload document |
| DELETE | `/documents/{id}` | Delete document |

### Admin Service (`:8082`)

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Health check |
| GET | `/cache/stats` | Cache statistics |
| POST | `/cache/clear` | Clear cache |
| GET | `/stats` | System statistics |

### API Gateway (`:8080`)

Routes all above endpoints with `/api/v1` prefix.

---

## Environment Variables

```bash
# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=docintel
POSTGRES_USER=docintel
POSTGRES_PASSWORD=docintel

# MinIO
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=documents

# Qdrant
QDRANT_URL=http://qdrant:6333

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434

# LiteLLM
LITELLM_MODEL=ollama/qwen3:4b
LITELLM_FALLBACKS=ollama/phi3:mini

# Embedding
EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2

# Langfuse (optional)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://langfuse:3000
```

---

## Running the Application

### First-Time Setup
```bash
./scripts/setup.sh
```

### Daily Start
```bash
./scripts/start-app.sh
```

### Manual Docker Commands
```bash
# Start infrastructure only
docker compose up -d

# Start with app services
docker compose up -d

# Rebuild specific service
docker compose up -d --build rag-service

# View logs
docker compose logs -f rag-service
```

---

## Testing

### Unit Tests
```bash
# RAG Service
cd services/rag-service && pytest

# Kotlin services
cd services/document-service && ./gradlew test
cd services/api-gateway && ./gradlew test
cd services/admin-service && ./gradlew test
```

### E2E Tests (Web UI)
```bash
cd services/web-ui && npx playwright test
```

### Integration Tests
Require Docker with Testcontainers:
```bash
TESTCONTAINERS_ENABLED=true ./gradlew test
```

---

## Key Design Decisions

1. **Qdrant for Everything Vector**: Both document chunks and response cache use Qdrant, simplifying the stack.

2. **Semantic Cache**: Query embeddings are compared to cached queries (threshold 0.92) for sub-second responses.

3. **Cache Invalidation on Indexing**: When new documents are added, the response cache is cleared to ensure fresh answers.

4. **Domain-Aware Routing**: Documents are classified into domains (technical, hr_policy, contracts) for better retrieval.

5. **Multi-Tenant Isolation**: All data is filtered by `tenant_id` at query time.

6. **Local LLM (Ollama)**: No external API dependencies, full data privacy, zero inference costs.

7. **SSE Streaming**: Real-time token streaming for better UX during generation.

8. **Sample Datasets**: HuggingFace integration allows quick demos without manual data prep.
