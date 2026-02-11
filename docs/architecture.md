# DocIntel Architecture

## System Overview

DocIntel is a multi-tenant document Q&A system built with a polyglot microservices architecture.

```
┌─────────────────────────────────────────────────────────────────┐
│                         Clients                                  │
│                    (Web, Mobile, API)                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      API Gateway                                 │
│                   (Kotlin/Spring Boot)                           │
│  • JWT Authentication  • Rate Limiting  • Request Routing        │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   Document    │    │     RAG       │    │    Admin      │
│   Service     │    │   Service     │    │   Service     │
│   (Kotlin)    │    │   (Python)    │    │   (Kotlin)    │
│               │    │               │    │               │
│ • Upload      │    │ • Query       │    │ • Tenants     │
│ • Extract     │    │ • Index       │    │ • Users       │
│ • Chunk       │    │ • Route       │    │ • Stats       │
└───────────────┘    └───────────────┘    └───────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Data Layer                                 │
├─────────────┬─────────────┬─────────────┬─────────────┬─────────┤
│ PostgreSQL  │   Qdrant    │    Redis    │    MinIO    │ Ollama  │
│ (metadata)  │  (vectors)  │   (cache)   │  (files)    │ (LLM)   │
└─────────────┴─────────────┴─────────────┴─────────────┴─────────┘
```

## Component Details

### API Gateway (Kotlin/Spring Boot)

**Responsibilities:**
- JWT authentication and authorization
- Rate limiting per tenant
- Request routing to backend services
- Response aggregation

**Key Technologies:**
- Spring Cloud Gateway
- Spring Security OAuth2
- Redis for rate limiting

### Document Service (Kotlin/Spring Boot)

**Responsibilities:**
- File upload and storage
- Text extraction (PDF, DOCX, TXT)
- Triggers chunking and indexing

**Key Technologies:**
- Apache Tika for extraction
- MinIO for file storage
- PostgreSQL for metadata

### RAG Service (Python/FastAPI/Haystack)

**Responsibilities:**
- Document indexing and embedding
- Query processing with domain routing
- Hybrid search and reranking
- Response generation via LLM

**Key Technologies:**
- Haystack 2.x for pipelines
- LiteLLM for LLM abstraction
- Chonkie for chunking
- Qdrant for vectors

### Admin Service (Kotlin/Spring Boot)

**Responsibilities:**
- Tenant management
- User and role management
- System statistics
- Cache management

## RAG Pipeline Architecture

```
Query
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ TransformersZeroShotTextRouter (DeBERTa)                    │
│ → Classifies: hr_policy | technical | contracts | general   │
└─────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ DomainFilterBuilder                                          │
│ → Converts classification to Qdrant filter                   │
└─────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ QueryExpander                                                │
│ → Adds synonyms for vocabulary gap mitigation                │
└─────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│ SentenceTransformersTextEmbedder (nomic-embed-text)         │
└─────────────────────────────────────────────────────────────┘
  │
  ├─────────────────────────────────────┐
  ▼                                     ▼
┌───────────────────┐          ┌───────────────────┐
│ SemanticCacheCheck│          │  SecureRetriever  │
│ (similarity > 0.92)│          │ (tenant + domain  │
└───────────────────┘          │  + role filters)  │
  │                            └───────────────────┘
  │ (cache miss)                        │
  │                                     ▼
  │                            ┌───────────────────┐
  │                            │   BM25 Retriever  │
  │                            └───────────────────┘
  │                                     │
  │                                     ▼
  │                            ┌───────────────────┐
  │                            │ DocumentJoiner    │
  │                            │ (RRF Fusion)      │
  │                            └───────────────────┘
  │                                     │
  │                                     ▼
  │                            ┌───────────────────┐
  │                            │ TransformersRanker│
  │                            │ (MiniLM reranker) │
  │                            └───────────────────┘
  │                                     │
  └─────────────────────────────────────┤
                                        ▼
                               ┌───────────────────┐
                               │  PromptBuilder    │
                               └───────────────────┘
                                        │
                                        ▼
                               ┌───────────────────┐
                               │ LiteLLMGenerator  │
                               │ (Qwen3-4B/Phi-3)  │
                               └───────────────────┘
                                        │
                                        ▼
                               ┌───────────────────┐
                               │ SemanticCacheWrite│
                               │ + CostTracker     │
                               └───────────────────┘
                                        │
                                        ▼
                                    Response
```

## Security Model

### Multi-Tenancy

- Every document and query is scoped to a `tenant_id`
- Tenant isolation enforced at database query level (Qdrant filters)
- Cross-tenant access is impossible by design

### Role-Based Access Control

Documents can have:
- `allowed_roles`: List of roles that can access
- `allowed_users`: List of specific user IDs

SecureRetriever applies these filters before retrieval.

### Audit Trail

All queries are logged with:
- User ID and tenant
- Query text and detected domain
- Response metadata
- Cost tracking

Langfuse provides full observability of LLM interactions.

## Data Flow

### Document Ingestion

```
Client → API Gateway → Document Service → MinIO (store file)
                                       → PostgreSQL (metadata)
                                       → RAG Service (chunk + embed)
                                       → Qdrant (store vectors)
```

### Query Processing

```
Client → API Gateway → RAG Service → Qdrant (retrieve)
                                  → Ollama (generate)
                                  → Langfuse (trace)
       ← API Gateway ← RAG Service ← Response
```

## Deployment

### Local Development

```bash
docker compose --profile cpu up -d
```

### Production Considerations

- Use managed services (Cloud SQL, Qdrant Cloud, etc.)
- Enable HTTPS at the gateway
- Use cloud LLM APIs for reliability
- Set up proper monitoring and alerting
