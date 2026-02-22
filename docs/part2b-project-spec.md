# Part 2B: Enterprise Document Intelligence
## Detailed Project Specification

**Purpose:** This document contains detailed component specifications for building the Enterprise Document Intelligence system. Each component spec is designed to be "AI-coding ready" - clear enough to paste into Cursor or Claude Code for implementation.

**Companion to:** `part2b-enterprise-document-intelligence.md` (the blog post)

---

## System Overview

A production-grade document Q&A system that:
- Ingests multiple document formats (PDF, DOCX, TXT, MD)
- Provides hybrid search (dense + sparse + reranking)
- Supports multi-tenant isolation
- Implements semantic caching
- Runs entirely local (no API costs)

---

## Component Specifications

### Component 1: API Gateway

**Responsibility:** Single entry point for all client requests. Handles authentication, rate limiting, request routing, and response aggregation.

**Technology:** Kotlin 1.9+, Spring Boot 3.2+, Spring Security, Spring Cloud Gateway

**Port:** 8080

**Dependencies:**
```kotlin
// build.gradle.kts
dependencies {
    implementation("org.springframework.boot:spring-boot-starter-webflux")
    implementation("org.springframework.cloud:spring-cloud-starter-gateway")
    implementation("org.springframework.boot:spring-boot-starter-security")
    implementation("org.springframework.boot:spring-boot-starter-oauth2-resource-server")
    implementation("io.micrometer:micrometer-registry-prometheus")
}
```

**API Endpoints:**

```yaml
# Document Operations (routes to Document Service :8081)
POST   /api/v1/documents
  - Request: multipart/form-data with file + metadata JSON
  - Response: DocumentResponse
  - Auth: Bearer token with tenant_id claim
  
GET    /api/v1/documents
  - Query params: page, size, status
  - Response: Page<DocumentResponse>
  
GET    /api/v1/documents/{id}
  - Response: DocumentResponse with chunk metadata
  
DELETE /api/v1/documents/{id}
  - Response: 204 No Content

# Query Operations (routes to RAG Service :8000)
POST   /api/v1/query
  - Request: QueryRequest
  - Response: QueryResponse
  
POST   /api/v1/query/stream
  - Request: QueryRequest
  - Response: Server-Sent Events (text/event-stream)

# Admin Operations (routes to Admin Service :8082)
GET    /api/v1/admin/health
GET    /api/v1/admin/metrics
POST   /api/v1/admin/cache/clear
GET    /api/v1/tenants/{id}/stats
```

**Data Models:**

```kotlin
// requests.kt
data class DocumentUploadRequest(
    val metadata: Map<String, String>? = null
)

data class QueryRequest(
    val question: String,
    val topK: Int = 5,
    val useReranking: Boolean = true,
    val useCache: Boolean = true
)

// responses.kt
data class DocumentResponse(
    val id: UUID,
    val filename: String,
    val contentType: String,
    val fileSize: Long,
    val chunkCount: Int,
    val status: ProcessingStatus,
    val createdAt: Instant,
    val updatedAt: Instant
)

data class QueryResponse(
    val answer: String,
    val sources: List<SourceDocument>,
    val cached: Boolean,
    val latencyMs: Long
)

data class SourceDocument(
    val chunkId: String,
    val documentId: String,
    val filename: String,
    val content: String,
    val score: Float,
    val metadata: Map<String, Any>
)

enum class ProcessingStatus {
    PENDING, PROCESSING, COMPLETED, FAILED
}
```

**Configuration:**

```yaml
# application.yml
spring:
  cloud:
    gateway:
      routes:
        - id: document-service
          uri: http://document-service:8081
          predicates:
            - Path=/api/v1/documents/**
        - id: rag-service
          uri: http://rag-service:8000
          predicates:
            - Path=/api/v1/query/**
        - id: admin-service
          uri: http://admin-service:8082
          predicates:
            - Path=/api/v1/admin/**,/api/v1/tenants/**

  security:
    oauth2:
      resourceserver:
        jwt:
          # For local dev, use simple JWT validation
          # For production, configure proper issuer-uri
          public-key-location: classpath:public-key.pem

# Rate limiting per tenant
rate-limiting:
  default-limit: 100  # requests per minute
  query-limit: 20     # expensive RAG queries per minute
```

**Key Design Decisions:**
1. **Reactive Gateway**: Spring WebFlux for non-blocking I/O, handles concurrent requests efficiently
2. **JWT with tenant_id**: Stateless auth, tenant isolation enforced at gateway level
3. **Route-based rate limiting**: Different limits for cheap (list) vs expensive (query) operations
4. **Metrics endpoint**: Prometheus format for Grafana dashboards

---

### Component 2: Document Service

**Responsibility:** Handles document upload, storage, text extraction, and chunking. Triggers embedding via RAG Service after processing.

**Technology:** Kotlin 1.9+, Spring Boot 3.2+, Apache Tika, kotlinx.coroutines

**Port:** 8081

**Dependencies:**
```kotlin
dependencies {
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")
    implementation("org.apache.tika:tika-core:2.9.1")
    implementation("org.apache.tika:tika-parsers-standard-package:2.9.1")
    implementation("io.minio:minio:8.5.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.8.0")
    runtimeOnly("org.postgresql:postgresql")
}
```

**Internal API Endpoints:**

```yaml
POST   /internal/documents
  - Request: DocumentCreateRequest (multipart)
  - Response: DocumentResponse
  - Triggers: Text extraction, chunking, then calls RAG Service /embed

GET    /internal/documents/{id}
  - Response: DocumentDetailResponse (includes chunks)

DELETE /internal/documents/{id}
  - Cascades: Deletes chunks, MinIO file, calls RAG Service to delete vectors

GET    /internal/documents/{id}/chunks
  - Response: List<ChunkResponse>

POST   /internal/documents/{id}/reprocess
  - Triggers: Re-extraction and re-chunking with different strategy
```

**Data Models:**

```kotlin
// entities.kt
@Entity
@Table(name = "documents")
data class Document(
    @Id
    val id: UUID = UUID.randomUUID(),
    
    @Column(name = "tenant_id", nullable = false)
    val tenantId: String,
    
    @Column(nullable = false)
    val filename: String,
    
    @Column(name = "content_type")
    val contentType: String?,
    
    @Column(name = "file_size")
    val fileSize: Long,
    
    @Column(name = "file_path", nullable = false)
    val filePath: String,  // MinIO object key
    
    @Enumerated(EnumType.STRING)
    val status: ProcessingStatus = ProcessingStatus.PENDING,
    
    @Column(name = "chunk_count")
    val chunkCount: Int = 0,
    
    @Type(JsonType::class)
    @Column(columnDefinition = "jsonb")
    val metadata: Map<String, String> = emptyMap(),
    
    @Column(name = "error_message")
    val errorMessage: String? = null,
    
    @Column(name = "created_at")
    val createdAt: Instant = Instant.now(),
    
    @Column(name = "updated_at")
    val updatedAt: Instant = Instant.now()
)

@Entity
@Table(name = "chunks")
data class Chunk(
    @Id
    val id: UUID = UUID.randomUUID(),
    
    @Column(name = "document_id", nullable = false)
    val documentId: UUID,
    
    @Column(name = "tenant_id", nullable = false)
    val tenantId: String,
    
    @Column(columnDefinition = "TEXT", nullable = false)
    val content: String,
    
    @Column(name = "chunk_index", nullable = false)
    val chunkIndex: Int,
    
    @Column(name = "start_char")
    val startChar: Int,
    
    @Column(name = "end_char")
    val endChar: Int,
    
    @Column(name = "token_count")
    val tokenCount: Int,
    
    @Type(JsonType::class)
    @Column(columnDefinition = "jsonb")
    val metadata: Map<String, Any> = emptyMap(),
    
    @Column(name = "created_at")
    val createdAt: Instant = Instant.now()
)
```

**Chunking Service (Python - RAG Service):**

Chunking is delegated to the RAG Service (Python) using **Chonkie** library for 
production-grade chunking. This decision leverages Python's superior NLP ecosystem.

```python
# chunking.py (in RAG Service)
from chonkie import RecursiveChunker, SemanticChunker, TokenChunker
from chonkie.embeddings import OllamaEmbeddings
from pydantic import BaseModel
from enum import Enum

class ChunkingMethod(str, Enum):
    RECURSIVE = "recursive"    # Best balance of quality and speed
    SEMANTIC = "semantic"      # Embedding-based topic boundaries
    TOKEN = "token"            # Fixed token count

class ChunkingConfig(BaseModel):
    method: ChunkingMethod = ChunkingMethod.RECURSIVE
    chunk_size: int = 400      # tokens
    chunk_overlap: int = 0
    
class ChunkResult(BaseModel):
    content: str
    start_char: int
    end_char: int
    token_count: int
    metadata: dict = {}

class ChunkingService:
    """
    Production chunking using Chonkie library.
    Supports multiple strategies with consistent interface.
    """
    
    def __init__(self, embedding_model: str = "nomic-embed-text"):
        # For semantic chunking (nomic-embed-text: Apache 2.0, MTEB 62.39)
        self.embeddings = OllamaEmbeddings(model=embedding_model)
        
        # Pre-configured chunkers
        self.chunkers = {
            ChunkingMethod.RECURSIVE: RecursiveChunker(
                chunk_size=400,
                chunk_overlap=0
            ),
            ChunkingMethod.SEMANTIC: SemanticChunker(
                embedding_model=self.embeddings,
                chunk_size=400,
                threshold=0.5
            ),
            ChunkingMethod.TOKEN: TokenChunker(
                chunk_size=400,
                chunk_overlap=0
            )
        }
    
    def chunk(self, text: str, config: ChunkingConfig) -> list[ChunkResult]:
        chunker = self.chunkers.get(config.method, self.chunkers[ChunkingMethod.RECURSIVE])
        
        # Chonkie returns Chunk objects with text, start_index, end_index, token_count
        chunks = chunker.chunk(text)
        
        return [
            ChunkResult(
                content=chunk.text,
                start_char=chunk.start_index,
                end_char=chunk.end_index,
                token_count=chunk.token_count
            )
            for chunk in chunks
        ]
```

**Why Chonkie over custom implementation:**
- Production-tested across thousands of deployments
- Multiple chunking algorithms (Recursive, Semantic, Token, Code, Table)
- Built-in embedding provider integrations (Ollama, OpenAI, etc.)
- MIT license, actively maintained
- Handles edge cases (tables, code blocks, markdown) properly

**Integration Points:**
- **MinIO**: Store raw files at `documents/{tenant_id}/{document_id}/original.{ext}`
- **PostgreSQL**: Store document and chunk metadata
- **RAG Service**: Call `POST /embed` after chunking completes

**Key Design Decisions:**
1. **Apache Tika**: Format-agnostic extraction (PDF, DOCX, TXT, MD, HTML)
2. **Recursive chunking default**: Per 2A research, best balance of quality and speed
3. **400 tokens, 0 overlap**: Chroma research findings from 2A
4. **Async processing**: Upload returns immediately, processing happens in background
5. **Store original file**: Enables re-chunking with different strategies later

---

### Component 3: RAG Service

**Responsibility:** Core RAG pipeline - embedding, retrieval, reranking, generation. Uses Haystack for pipeline orchestration, LiteLLM for LLM abstraction.

**Technology:** Python 3.11+, FastAPI, Haystack 2.x, LiteLLM, sentence-transformers

**Port:** 8000

**Dependencies:**
```toml
# pyproject.toml
[project]
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "haystack-ai>=2.9.0",
    "qdrant-haystack>=6.0.0",
    "qdrant-client>=1.16.0",
    "litellm>=1.56.0",
    "sentence-transformers>=3.3.0",
    "langfuse>=2.57.0",
    "chonkie>=1.5.0",            # Production chunking library
    "pydantic>=2.10.0",
    "datasets>=3.2.0",           # For RAGBench evaluation
    "ragas>=0.2.0",              # RAG evaluation framework
]
```

**API Endpoints:**

```yaml
POST   /query
  - Request: QueryRequest
  - Response: QueryResponse
  
POST   /query/stream
  - Request: QueryRequest
  - Response: StreamingResponse (Server-Sent Events)

POST   /embed
  - Request: EmbedRequest
  - Response: EmbedResponse
  - Called by: Document Service after chunking

POST   /embed/batch
  - Request: BatchEmbedRequest
  - Response: BatchEmbedResponse

DELETE /index/{tenant_id}
  - Deletes all vectors for tenant
  
DELETE /index/{tenant_id}/{document_id}
  - Deletes vectors for specific document

GET    /health
  - Response: HealthResponse
```

**Data Models:**

```python
# models.py
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

class QueryRequest(BaseModel):
    question: str
    tenant_id: str
    user_id: Optional[str] = None
    top_k: int = Field(default=5, ge=1, le=20)
    use_reranking: bool = True
    use_cache: bool = True

class QueryResponse(BaseModel):
    answer: str
    sources: list["SourceDocument"]
    cached: bool
    retrieval_scores: list[float]
    model_used: str
    latency_ms: int

class SourceDocument(BaseModel):
    chunk_id: str
    document_id: str
    content: str
    score: float
    metadata: dict

class EmbedRequest(BaseModel):
    chunks: list["ChunkData"]
    tenant_id: str
    document_id: str

class ChunkData(BaseModel):
    chunk_id: str
    content: str
    metadata: dict = Field(default_factory=dict)

class EmbedResponse(BaseModel):
    embedded_count: int
    collection: str
```

**Haystack Pipeline Configuration:**

```python
# pipelines.py
from haystack import Pipeline
from haystack.components.embedders import (
    SentenceTransformersTextEmbedder,
    SentenceTransformersDocumentEmbedder
)
from haystack.components.writers import DocumentWriter
from haystack.components.joiners import DocumentJoiner
from haystack.components.rankers import TransformersSimilarityRanker
from haystack_integrations.components.retrievers.qdrant import QdrantEmbeddingRetriever
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from .components import (
    LiteLLMGenerator,
    SemanticCacheChecker,
    SemanticCacheWriter,
    QdrantBM25Retriever,
    PromptBuilder,
    TenantFilter
)

# Configuration
# =============================================================================
# MODEL SELECTION: Optimized for M1 MacBook Air 16GB RAM / NVIDIA GPU systems
# All models are open source with permissive licenses (MIT/Apache 2.0)
# =============================================================================

# Embedding: nomic-embed-text (Apache 2.0)
# - 137M params, fits easily in 16GB RAM
# - MTEB score: 62.39 (better than Arctic's 55.98)
# - 8192 token context, 768 dimensions
# - Fully open: weights, code, and training data available
EMBEDDING_MODEL = "nomic-embed-text"  # via Ollama

# Reranker: cross-encoder MiniLM (Apache 2.0)
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# LLM: Qwen3-4B (Apache 2.0 license) - Best balance of quality and size
# - 4B params, ~5GB RAM usage (Q4 quantized)
# - Apache 2.0: Fully commercial, no restrictions
# - Excellent reasoning and instruction following
# - Hybrid thinking mode (can toggle /think for complex queries)
LLM_MODEL = "ollama/qwen3:4b"

# Fallbacks: All Apache 2.0 or MIT licensed
LLM_FALLBACKS = ["ollama/phi3:mini", "ollama/qwen3:1.7b"]

def create_document_store() -> QdrantDocumentStore:
    return QdrantDocumentStore(
        url="http://qdrant:6333",
        index="documents",
        embedding_dim=768,  # BGE-base dimension
        similarity="cosine",
        recreate_index=False
    )

def create_indexing_pipeline(document_store: QdrantDocumentStore) -> Pipeline:
    """Pipeline for embedding and storing document chunks."""
    pipeline = Pipeline()
    
    pipeline.add_component(
        "embedder",
        SentenceTransformersDocumentEmbedder(model=EMBEDDING_MODEL)
    )
    pipeline.add_component(
        "writer",
        DocumentWriter(document_store=document_store)
    )
    
    pipeline.connect("embedder", "writer")
    return pipeline

def create_query_pipeline(document_store: QdrantDocumentStore) -> Pipeline:
    """
    Pipeline for RAG query processing with domain-aware routing.
    
    Flow:
      Query → Router → DomainFilterBuilder → Embedder → SecureRetriever
                                                ↓
                                          Cache Check
                                                ↓
                                     Joiner → Reranker → LLM
    
    All components in one pipeline = single run() call, full Langfuse tracing.
    """
    pipeline = Pipeline()
    
    # ==========================================================================
    # Stage 1: Domain Classification (Haystack built-in router)
    # ==========================================================================
    pipeline.add_component("domain_router", TransformersZeroShotTextRouter(
        model="MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33",
        labels=DOMAIN_LABELS  # ["hr_policy", "technical", "contracts", "general"]
    ))
    pipeline.add_component("filter_builder", DomainFilterBuilder(
        filter_field="document_type"
    ))
    
    # ==========================================================================
    # Stage 2: Query Expansion (optional, for vocabulary gap mitigation)
    # ==========================================================================
    pipeline.add_component("query_expander", QueryExpander(
        llm_model="ollama/qwen3:1.7b",
        enabled=True  # Set False to disable expansion
    ))
    
    # ==========================================================================
    # Stage 3: Embedding + Caching
    # ==========================================================================
    pipeline.add_component("embedder", SentenceTransformersTextEmbedder(
        model=EMBEDDING_MODEL
    ))
    pipeline.add_component("cache_checker", SemanticCacheChecker(
        qdrant_url="http://qdrant:6333",
        collection="response_cache",
        threshold=0.92
    ))
    
    # ==========================================================================
    # Stage 4: Hybrid Retrieval with Domain + ACL Filtering
    # ==========================================================================
    pipeline.add_component("secure_retriever", SecureRetriever(
        document_store=document_store,
        top_k=50
    ))
    pipeline.add_component("bm25_retriever", QdrantBM25Retriever(
        document_store=document_store,
        top_k=50
    ))
    pipeline.add_component("joiner", DocumentJoiner(
        join_mode="reciprocal_rank_fusion",
        top_k=20
    ))
    
    # ==========================================================================
    # Stage 5: Reranking + Generation
    # ==========================================================================
    pipeline.add_component("reranker", TransformersSimilarityRanker(
        model=RERANKER_MODEL,
        top_k=5
    ))
    pipeline.add_component("prompt_builder", PromptBuilder(
        template=RAG_PROMPT_TEMPLATE
    ))
    pipeline.add_component("llm", LiteLLMGenerator(
        model=LLM_MODEL,
        fallbacks=LLM_FALLBACKS
    ))
    
    # ==========================================================================
    # Stage 6: Cache Write + Cost Tracking
    # ==========================================================================
    pipeline.add_component("cache_writer", SemanticCacheWriter(
        qdrant_url="http://qdrant:6333",
        collection="response_cache"
    ))
    pipeline.add_component("cost_tracker", CostTracker())
    
    # ==========================================================================
    # Pipeline Connections
    # ==========================================================================
    
    # Router → Filter Builder (router outputs to one of 4 labels)
    pipeline.connect("domain_router.hr_policy", "filter_builder.hr_policy")
    pipeline.connect("domain_router.technical", "filter_builder.technical")
    pipeline.connect("domain_router.contracts", "filter_builder.contracts")
    pipeline.connect("domain_router.general", "filter_builder.general")
    
    # Filter Builder → Query Expander → Embedder
    pipeline.connect("filter_builder.query", "query_expander.query")
    pipeline.connect("query_expander.expanded_query", "embedder.text")
    
    # Embedder → Cache Check + Retrieval
    pipeline.connect("embedder.embedding", "cache_checker.query_embedding")
    pipeline.connect("embedder.embedding", "secure_retriever.query_embedding")
    pipeline.connect("filter_builder.domain_filter", "secure_retriever.domain_filter")
    
    # Hybrid Retrieval → Joiner
    pipeline.connect("secure_retriever.documents", "joiner.documents")
    pipeline.connect("bm25_retriever.documents", "joiner.documents")
    
    # Joiner → Reranker → Prompt → LLM
    pipeline.connect("joiner.documents", "reranker.documents")
    pipeline.connect("query_expander.original_query", "reranker.query")
    pipeline.connect("reranker.documents", "prompt_builder.documents")
    pipeline.connect("query_expander.original_query", "prompt_builder.query")
    pipeline.connect("prompt_builder.prompt", "llm.prompt")
    
    return pipeline

RAG_PROMPT_TEMPLATE = """
Answer the question based on the provided context. If the context doesn't 
contain enough information to answer, say so clearly.

Context:
{% for doc in documents %}
---
Source: {{ doc.meta.filename }} (chunk {{ doc.meta.chunk_index }})
{{ doc.content }}
{% endfor %}
---

Question: {{ query }}

Answer:
"""


# =============================================================================
# Pipeline Execution Example
# =============================================================================

def query_documents(
    pipeline: Pipeline,
    question: str,
    tenant_id: str,
    user_roles: list[str] = None,
    user_id: str = None,
    document_type: str = None  # Optional explicit domain filter
) -> dict:
    """
    Execute the full RAG pipeline.
    
    Args:
        question: User's question
        tenant_id: Tenant for isolation
        user_roles: User's roles for ACL filtering
        user_id: User ID for user-specific ACL
        document_type: Optional domain filter ("hr_policy", "technical", "contracts")
                       If None, router auto-detects domain
    
    Returns:
        {
            "answer": str,
            "sources": list[dict],
            "detected_domain": str,
            "cost_usd": float,
            "cache_hit": bool
        }
    """
    result = pipeline.run(
        {
            # Router input
            "domain_router": {"text": question},
            
            # Override domain if explicitly specified
            "filter_builder": {"explicit_domain": document_type},
            
            # Security context (passed to SecureRetriever)
            "secure_retriever": {
                "tenant_id": tenant_id,
                "user_roles": user_roles or [],
                "user_id": user_id
            },
            
            # BM25 also needs the query
            "bm25_retriever": {"query": question},
            
            # Cost tracking
            "cost_tracker": {"tenant_id": tenant_id}
        },
        include_outputs_from={
            "llm", "filter_builder", "cache_checker", "cost_tracker"
        }
    )
    
    return {
        "answer": result["llm"]["replies"][0],
        "sources": [doc.meta for doc in result.get("reranker", {}).get("documents", [])],
        "detected_domain": result["filter_builder"]["detected_domain"],
        "cost_usd": result["cost_tracker"]["cost_usd"],
        "cache_hit": result["cache_checker"].get("cache_hit", False)
    }


# Usage example:
# response = query_documents(
#     pipeline=pipeline,
#     question="What is the vacation policy?",
#     tenant_id="acme_corp",
#     user_roles=["employee", "hr"],
#     document_type=None  # Auto-detect → will route to "hr_policy"
# )
# print(response["answer"])
# print(f"Detected domain: {response['detected_domain']}")  # "hr_policy"
```

**Custom Haystack Components:**

```python
# components.py
from haystack import component, Document
from typing import Optional
import litellm
from qdrant_client import QdrantClient

@component
class LiteLLMGenerator:
    """
    Haystack component wrapping LiteLLM for provider flexibility.
    Same code works with Ollama (local) or cloud APIs.
    """
    def __init__(
        self, 
        model: str = "ollama/llama3.2",
        fallbacks: list[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        api_base: str = "http://ollama:11434"
    ):
        self.model = model
        self.fallbacks = fallbacks or []
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Set Ollama base URL
        if model.startswith("ollama/"):
            litellm.api_base = api_base
    
    @component.output_types(replies=list[str], meta=dict)
    def run(self, prompt: str):
        response = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            fallbacks=self.fallbacks,
            temperature=self.temperature,
            max_tokens=self.max_tokens
        )
        return {
            "replies": [response.choices[0].message.content],
            "meta": {
                "model": response.model,
                "usage": dict(response.usage) if response.usage else {}
            }
        }


@component
class SemanticCacheChecker:
    """
    Check Qdrant for semantically similar cached responses.
    Returns cached response if similarity > threshold.
    Uses query_points() API (qdrant-client v1.16+).
    """
    def __init__(
        self,
        qdrant_url: str,
        collection: str = "response_cache",
        threshold: float = 0.92
    ):
        self.client = QdrantClient(url=qdrant_url)
        self.collection = collection
        self.threshold = threshold
    
    @component.output_types(
        cache_hit=bool,
        cached_response=Optional[str],
        cached_sources=Optional[list],
        query_embedding=list[float]
    )
    def run(self, query_embedding: list[float], tenant_id: str):
        from qdrant_client import models
        
        results = self.client.query_points(
            collection_name=self.collection,
            query=query_embedding,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id)
                    )
                ]
            ),
            limit=1,
            score_threshold=self.threshold
        )
        
        if results.points:
            payload = results.points[0].payload
            return {
                "cache_hit": True,
                "cached_response": payload["response"],
                "cached_sources": payload.get("sources", []),
                "query_embedding": query_embedding
            }
        
        return {
            "cache_hit": False,
            "cached_response": None,
            "cached_sources": None,
            "query_embedding": query_embedding
        }


@component
class SemanticCacheWriter:
    """Write successful responses to semantic cache."""
    
    def __init__(self, qdrant_url: str, collection: str = "response_cache"):
        self.client = QdrantClient(url=qdrant_url)
        self.collection = collection
    
    @component.output_types(cached=bool)
    def run(
        self,
        query: str,
        query_embedding: list[float],
        response: str,
        sources: list[dict],
        tenant_id: str
    ):
        import uuid
        from datetime import datetime
        
        self.client.upsert(
            collection_name=self.collection,
            points=[{
                "id": str(uuid.uuid4()),
                "vector": query_embedding,
                "payload": {
                    "query": query,
                    "response": response,
                    "sources": sources,
                    "tenant_id": tenant_id,
                    "created_at": datetime.utcnow().isoformat()
                }
            }]
        )
        return {"cached": True}


@component
class SecureRetriever:
    """
    Wraps retrieval with access control + domain filtering.
    Applies tenant + role + domain filters at the Qdrant query level.
    
    Combines:
      - Tenant isolation (required)
      - Role-based ACLs (optional)
      - Domain filtering from router (optional)
    
    See 2A/access_control_demo.ipynb for detailed patterns.
    """
    
    def __init__(self, document_store, top_k: int = 50):
        self.document_store = document_store
        self.top_k = top_k
    
    @component.output_types(documents=list[Document])
    def run(
        self, 
        query_embedding: list[float], 
        tenant_id: str,
        user_roles: list[str] = None,
        user_id: str = None,
        domain_filter: dict = None  # From DomainFilterBuilder
    ):
        from qdrant_client import models
        
        # Build ACL filter: tenant_id required
        must_conditions = [
            models.FieldCondition(
                key="tenant_id",
                match=models.MatchValue(value=tenant_id)
            )
        ]
        
        # Add domain filter if provided by router
        if domain_filter:
            must_conditions.append(
                models.FieldCondition(
                    key=domain_filter["key"],
                    match=models.MatchValue(value=domain_filter["match"]["value"])
                )
            )
        
        # Optional role-based filtering within tenant
        should_conditions = []
        if user_roles:
            should_conditions.append(
                models.FieldCondition(
                    key="allowed_roles",
                    match=models.MatchAny(any=user_roles)
                )
            )
        if user_id:
            should_conditions.append(
                models.FieldCondition(
                    key="allowed_users",
                    match=models.MatchValue(value=user_id)
                )
            )
        
        # Build complete filter
        query_filter = models.Filter(must=must_conditions)
        if should_conditions:
            query_filter.should = should_conditions
        
        # Retrieve with all filters applied at DB level
        results = self.document_store._client.query_points(
            collection_name=self.document_store.index,
            query=query_embedding,
            query_filter=query_filter,
            limit=self.top_k
        )
        
        # Convert to Haystack Documents
        documents = []
        for point in results.points:
            documents.append(Document(
                id=str(point.id),
                content=point.payload.get("content", ""),
                meta=point.payload,
                score=point.score
            ))
        
        return {"documents": documents}


@component  
class TenantFilter:
    """
    Legacy filter for post-retrieval tenant isolation.
    Prefer SecureRetriever for pre-filter ACLs.
    """
    
    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document], tenant_id: str):
        return {
            "documents": [
                doc for doc in documents
                if doc.meta.get("tenant_id") == tenant_id
            ]
        }


# =============================================================================
# NEW COMPONENTS: Patterns from 2A not yet in 2B
# These add ~15% coverage with low overhead
# =============================================================================

# =============================================================================
# DOMAIN ROUTING: Using Haystack's built-in TransformersZeroShotTextRouter
# =============================================================================
#
# Instead of custom routing, we use Haystack's native zero-shot classifier.
# This keeps everything in the pipeline for better maintainability and tracing.
#
# Model: MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33
# License: Apache 2.0
# Latency: ~50-100ms
# Accuracy: High (trained on NLI tasks)

from haystack.components.routers import TransformersZeroShotTextRouter

# Define domain labels for classification
DOMAIN_LABELS = ["hr_policy", "technical", "contracts", "general"]


@component
class DomainFilterBuilder:
    """
    Converts router classification output to Qdrant filter.
    Bridges TransformersZeroShotTextRouter → SecureRetriever.
    
    Pipeline flow:
      Query → Router → DomainFilterBuilder → SecureRetriever
                ↓              ↓
           (classifies)   (builds filter)
    """
    
    def __init__(self, filter_field: str = "document_type"):
        self.filter_field = filter_field
    
    @component.output_types(
        query=str,
        domain_filter=dict,
        detected_domain=str
    )
    def run(
        self, 
        hr_policy: str = None,
        technical: str = None, 
        contracts: str = None,
        general: str = None,
        explicit_domain: str = None  # User-specified override
    ):
        """
        Receives routed text from TransformersZeroShotTextRouter.
        Router outputs to exactly one of: hr_policy, technical, contracts, general
        
        Args:
            hr_policy: Query text if classified as HR policy
            technical: Query text if classified as technical
            contracts: Query text if classified as contracts
            general: Query text if classified as general/unknown
            explicit_domain: User-specified domain (overrides auto-detection)
        """
        # Determine which output received the query
        if explicit_domain and explicit_domain != "all":
            query = hr_policy or technical or contracts or general
            detected_domain = explicit_domain
        elif hr_policy:
            query = hr_policy
            detected_domain = "hr_policy"
        elif technical:
            query = technical
            detected_domain = "technical"
        elif contracts:
            query = contracts
            detected_domain = "contracts"
        else:
            query = general
            detected_domain = None  # No filter, search all domains
        
        # Build Qdrant filter (None means search all)
        domain_filter = None
        if detected_domain:
            domain_filter = {
                "key": self.filter_field,
                "match": {"value": detected_domain}
            }
        
        return {
            "query": query,
            "domain_filter": domain_filter,
            "detected_domain": detected_domain or "all"
        }


@component
class QueryExpander:
    """
    Expands user query with synonyms and related terms.
    Implements Query Transformation from 2A Section 3.4.
    
    Addresses vocabulary gap problem: "WFH" vs "remote work"
    """
    
    def __init__(self, llm_model: str = None, enabled: bool = True):
        self.llm_model = llm_model
        self.enabled = enabled
    
    @component.output_types(
        original_query=str,
        expanded_query=str,
        search_terms=list[str]
    )
    def run(self, query: str):
        if not self.enabled:
            return {
                "original_query": query,
                "expanded_query": query,
                "search_terms": [query]
            }
        
        import litellm
        
        prompt = f"""Given this search query, generate 2-3 alternative phrasings 
or related terms that might appear in documents. Keep it brief.

Query: {query}

Alternative terms (comma-separated):"""

        response = litellm.completion(
            model=self.llm_model or "ollama/qwen3:1.7b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=50
        )
        
        alternatives = response.choices[0].message.content.strip()
        terms = [t.strip() for t in alternatives.split(",")]
        
        # Combine original + expansions
        expanded = f"{query} {' '.join(terms)}"
        
        return {
            "original_query": query,
            "expanded_query": expanded,
            "search_terms": [query] + terms
        }


@component
class CostTracker:
    """
    Tracks LLM usage costs per query/tenant.
    Implements Cost Optimization from 2A Section 6.4.
    
    Uses LiteLLM's built-in cost tracking.
    """
    
    def __init__(self):
        self.costs = {}
    
    @component.output_types(
        response=str,
        cost_usd=float,
        tokens_used=dict
    )
    def run(self, response: str, tenant_id: str, litellm_response: dict = None):
        import litellm
        
        cost = 0.0
        tokens = {"prompt": 0, "completion": 0}
        
        if litellm_response:
            # Extract from LiteLLM response
            usage = litellm_response.get("usage", {})
            tokens = {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0)
            }
            
            # LiteLLM provides cost calculation
            cost = litellm.completion_cost(litellm_response) or 0.0
        
        # Aggregate per tenant
        if tenant_id not in self.costs:
            self.costs[tenant_id] = {"total_cost": 0.0, "query_count": 0}
        
        self.costs[tenant_id]["total_cost"] += cost
        self.costs[tenant_id]["query_count"] += 1
        
        return {
            "response": response,
            "cost_usd": cost,
            "tokens_used": tokens
        }
    
    def get_tenant_costs(self, tenant_id: str) -> dict:
        """Get accumulated costs for a tenant."""
        return self.costs.get(tenant_id, {"total_cost": 0.0, "query_count": 0})
```

**Langfuse Integration:**

```python
# tracing.py
from langfuse import Langfuse
from langfuse.decorators import observe, langfuse_context
import os

langfuse = Langfuse(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "pk-local"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY", "sk-local"),
    host=os.getenv("LANGFUSE_HOST", "http://langfuse:3000")
)

@observe()
def process_query(request: QueryRequest) -> QueryResponse:
    """Traced RAG query processing."""
    langfuse_context.update_current_trace(
        user_id=request.user_id,
        session_id=request.tenant_id,
        tags=["rag", "query"]
    )
    
    # Pipeline execution here...
    # All LLM calls automatically traced
```

**Key Design Decisions:**
1. **LiteLLM wrapper**: Enables local (Ollama) ↔ cloud swap via environment variables
2. **Qdrant dual-use**: Same infrastructure for document vectors and response cache
3. **Qwen3-4B LLM**: Apache 2.0 license, excellent quality, hybrid thinking mode
4. **Nomic-embed-text**: Apache 2.0, MTEB 62.39, 137M params, fully open (weights+data)
5. **Chonkie for chunking**: Production-grade library, multiple algorithms, MIT license
6. **Hybrid search + RRF**: Dense + sparse retrieval merged with Reciprocal Rank Fusion
7. **SecureRetriever pattern**: Pre-filter ACLs at database level for security
8. **MiniLM reranker**: Fast, local, Apache 2.0, provides significant quality boost
9. **DomainRouter**: Routes queries to appropriate document domains (2A Section 1.3)
10. **QueryExpander**: Addresses vocabulary gap with query transformation (2A Section 3.4)
11. **CostTracker**: Per-tenant cost tracking via LiteLLM (2A Section 6.4)
12. **Multi-domain datasets**: TechQA + HR Policies + CUAD for comprehensive evaluation
13. **Semantic cache at 0.92 threshold**: High enough to avoid false positives
14. **Hardware flexibility**: Docker profiles for CPU (M1) and GPU (NVIDIA)

**2A Concept Coverage: ~90%**

**License Summary (All Commercial-OK, No Restrictions):**
- Haystack: Apache 2.0 | LiteLLM: MIT | Chonkie: MIT | RAGAS: Apache 2.0
- **Qwen3-4B: Apache 2.0** | Phi-3 Mini: MIT
- Nomic-embed-text: Apache 2.0 | MiniLM: Apache 2.0

---

### Component 4: Admin Service

**Responsibility:** System administration - health monitoring, cache management, tenant statistics, metrics export.

**Technology:** Kotlin 1.9+, Spring Boot 3.2+, Micrometer

**Port:** 8082

**API Endpoints:**

```yaml
GET    /internal/health
  - Response: SystemHealth with component status

GET    /internal/metrics
  - Response: Prometheus format metrics

GET    /internal/stats
  - Response: SystemStats

POST   /internal/cache/clear
  - Clears all semantic cache entries

POST   /internal/cache/clear/{tenant_id}
  - Clears cache for specific tenant

GET    /internal/cache/stats
  - Response: CacheStats

GET    /internal/tenants
  - Response: List<TenantSummary>

GET    /internal/tenants/{id}/usage
  - Response: TenantUsage
```

**Data Models:**

```kotlin
data class SystemHealth(
    val status: HealthStatus,
    val components: Map<String, ComponentHealth>,
    val timestamp: Instant
)

data class ComponentHealth(
    val name: String,
    val status: HealthStatus,
    val latencyMs: Long?,
    val message: String?
)

enum class HealthStatus { UP, DOWN, DEGRADED }

data class TenantUsage(
    val tenantId: String,
    val documentCount: Int,
    val chunkCount: Int,
    val totalQueries: Long,
    val queriesLast24h: Long,
    val cacheHitRate: Double,
    val storageBytes: Long,
    val lastQueryAt: Instant?
)

data class CacheStats(
    val totalEntries: Long,
    val hitRate: Double,
    val avgLatencySavedMs: Long,
    val oldestEntry: Instant?,
    val newestEntry: Instant?
)
```

---

### Component 5: Qdrant Configuration

**Collections:**

```python
# qdrant_setup.py
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance, 
    PayloadSchemaType, TextIndexParams
)

client = QdrantClient(url="http://qdrant:6333")

# Collection 1: Document Vectors
client.create_collection(
    collection_name="documents",
    vectors_config=VectorParams(
        size=768,  # BGE-base dimension
        distance=Distance.COSINE
    ),
    hnsw_config={
        "m": 16,
        "ef_construct": 100
    },
    on_disk_payload=True  # Large payloads on disk
)

# Create payload indexes for filtering
client.create_payload_index(
    collection_name="documents",
    field_name="tenant_id",
    field_schema=PayloadSchemaType.KEYWORD
)
client.create_payload_index(
    collection_name="documents",
    field_name="document_id",
    field_schema=PayloadSchemaType.KEYWORD
)
# Text index for BM25
client.create_payload_index(
    collection_name="documents",
    field_name="content",
    field_schema=TextIndexParams(
        type="text",
        tokenizer="word",
        min_token_len=2,
        max_token_len=15,
        lowercase=True
    )
)

# Collection 2: Semantic Response Cache
client.create_collection(
    collection_name="response_cache",
    vectors_config=VectorParams(
        size=768,
        distance=Distance.COSINE
    ),
    hnsw_config={
        "m": 16,
        "ef_construct": 64  # Smaller, faster index
    }
)

client.create_payload_index(
    collection_name="response_cache",
    field_name="tenant_id",
    field_schema=PayloadSchemaType.KEYWORD
)
```

---

### Component 6: PostgreSQL Schema

```sql
-- migrations/V1__initial_schema.sql

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";  -- pgvector

-- Tenants table
CREATE TABLE tenants (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    quota_documents INT DEFAULT 1000,
    quota_queries_per_day INT DEFAULT 10000,
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Documents table
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100),
    file_size BIGINT NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    chunk_count INT DEFAULT 0,
    chunking_config JSONB,
    metadata JSONB DEFAULT '{}',
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_documents_tenant ON documents(tenant_id);
CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_created ON documents(created_at);

-- Chunks table (metadata only - vectors in Qdrant)
CREATE TABLE chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
    content TEXT NOT NULL,
    chunk_index INT NOT NULL,
    start_char INT,
    end_char INT,
    token_count INT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_chunks_document ON chunks(document_id);
CREATE INDEX idx_chunks_tenant ON chunks(tenant_id);

-- Query audit log
CREATE TABLE query_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id),
    user_id VARCHAR(64),
    question TEXT NOT NULL,
    answer TEXT,
    source_chunk_ids UUID[],
    cached BOOLEAN DEFAULT FALSE,
    latency_ms INT,
    model_used VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_query_log_tenant ON query_log(tenant_id);
CREATE INDEX idx_query_log_created ON query_log(created_at);
CREATE INDEX idx_query_log_tenant_created ON query_log(tenant_id, created_at);

-- Update trigger for updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Insert default tenant for local development
INSERT INTO tenants (id, name) VALUES ('default', 'Default Tenant');
```

---

### Component 7: Web UI

**Responsibility:** Chat interface for document Q&A. Consumes the API Gateway's streaming endpoints. API-first design allows any client (web, mobile, CLI) to use the same backend.

**Technology:** SvelteKit 2.x, TypeScript, Tailwind CSS, @ai-sdk/svelte, svelte-ai-elements

**Port:** 5173 (dev), 4173 (preview), 3001 (production)

**Dependencies:**
```json
// package.json
{
  "name": "docintel-web-ui",
  "type": "module",
  "scripts": {
    "dev": "vite dev",
    "build": "vite build",
    "preview": "vite preview",
    "check": "svelte-kit sync && svelte-check --tsconfig ./tsconfig.json"
  },
  "dependencies": {
    "ai": "^4.0.0",
    "@ai-sdk/svelte": "^1.0.0"
  },
  "devDependencies": {
    "@sveltejs/adapter-node": "^5.0.0",
    "@sveltejs/kit": "^2.0.0",
    "@sveltejs/vite-plugin-svelte": "^4.0.0",
    "svelte": "^5.0.0",
    "svelte-check": "^4.0.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.0.0",
    "vite": "^5.0.0"
  }
}
```

**Architecture:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Clients                                     │
├─────────────────┬─────────────────┬─────────────────┬───────────────────┤
│   Web UI        │   Mobile App    │   CLI Tool      │   3rd Party       │
│   (SvelteKit)   │   (iOS/Android) │   (Python/Go)   │   Integrations    │
└────────┬────────┴────────┬────────┴────────┬────────┴─────────┬─────────┘
         │                 │                 │                  │
         │    All clients use the same API (REST + SSE)         │
         └─────────────────┴─────────────────┴──────────────────┘
                                    │
                                    ▼
                     ┌──────────────────────────────┐
                     │        API Gateway           │
                     │    POST /api/v1/query        │ ◀── JSON request
                     │    POST /api/v1/query/stream │ ◀── SSE streaming
                     └──────────────────────────────┘
```

**Key Pages:**

```
src/routes/
├── +layout.svelte          # App shell with sidebar
├── +page.svelte            # Chat interface (main)
├── documents/
│   └── +page.svelte        # Document management
├── settings/
│   └── +page.svelte        # User settings
└── api/                    # Optional: BFF proxy routes
    └── chat/+server.ts     # Proxy to API Gateway (if needed)
```

**Main Chat Component:**

```svelte
<!-- src/routes/+page.svelte -->
<script lang="ts">
  import { onMount } from 'svelte';
  
  let messages: Array<{role: string, content: string}> = [];
  let input = '';
  let isStreaming = false;
  let currentResponse = '';
  
  const API_BASE = 'http://localhost:8080';
  
  async function sendMessage() {
    if (!input.trim() || isStreaming) return;
    
    const userMessage = input;
    input = '';
    messages = [...messages, { role: 'user', content: userMessage }];
    isStreaming = true;
    currentResponse = '';
    
    try {
      const response = await fetch(`${API_BASE}/api/v1/query/stream`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Accept': 'text/event-stream'
        },
        body: JSON.stringify({ question: userMessage })
      });
      
      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      
      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value);
        // Parse SSE format: "data: {...}\n\n"
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = JSON.parse(line.slice(6));
            if (data.token) {
              currentResponse += data.token;
            }
          }
        }
      }
      
      messages = [...messages, { role: 'assistant', content: currentResponse }];
    } catch (error) {
      console.error('Stream error:', error);
    } finally {
      isStreaming = false;
      currentResponse = '';
    }
  }
</script>

<div class="flex flex-col h-screen bg-gray-50">
  <!-- Header -->
  <header class="bg-white border-b px-6 py-4">
    <h1 class="text-xl font-semibold">DocIntel</h1>
  </header>
  
  <!-- Messages -->
  <main class="flex-1 overflow-y-auto p-6 space-y-4">
    {#each messages as message}
      <div class="flex {message.role === 'user' ? 'justify-end' : 'justify-start'}">
        <div class="max-w-2xl px-4 py-2 rounded-lg {message.role === 'user' 
          ? 'bg-blue-600 text-white' 
          : 'bg-white border shadow-sm'}">
          {message.content}
        </div>
      </div>
    {/each}
    
    {#if isStreaming && currentResponse}
      <div class="flex justify-start">
        <div class="max-w-2xl px-4 py-2 rounded-lg bg-white border shadow-sm">
          {currentResponse}<span class="animate-pulse">▌</span>
        </div>
      </div>
    {/if}
  </main>
  
  <!-- Input -->
  <footer class="bg-white border-t p-4">
    <form on:submit|preventDefault={sendMessage} class="flex gap-2 max-w-3xl mx-auto">
      <input
        bind:value={input}
        placeholder="Ask a question about your documents..."
        class="flex-1 px-4 py-2 border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
        disabled={isStreaming}
      />
      <button
        type="submit"
        disabled={isStreaming || !input.trim()}
        class="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
      >
        {isStreaming ? 'Sending...' : 'Send'}
      </button>
    </form>
  </footer>
</div>
```

**SSE Response Format (from API Gateway):**

```typescript
// Each SSE event contains one of these:
interface StreamEvent {
  // Token stream
  token?: string;
  
  // Metadata (sent at start)
  metadata?: {
    query_id: string;
    detected_domain: string;
    cache_hit: boolean;
  };
  
  // Sources (sent at end)
  sources?: Array<{
    document_id: string;
    filename: string;
    chunk_index: number;
    score: number;
  }>;
  
  // Completion signal
  done?: boolean;
}
```

**Dockerfile:**

```dockerfile
# Dockerfile
FROM node:22-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
COPY --from=builder /app/build ./build
COPY --from=builder /app/package*.json ./
RUN npm ci --production
ENV NODE_ENV=production
ENV PORT=3001
EXPOSE 3001
CMD ["node", "build"]
```

**Environment Variables:**

```bash
# .env
PUBLIC_API_URL=http://localhost:8080    # API Gateway URL
PUBLIC_APP_NAME=DocIntel                # App display name
```

---

### Feature: Sample Dataset Loading

Allow users to quickly seed the system with sample documents from HuggingFace datasets.

**API Endpoint (RAG Service):**

```python
# POST /sample-datasets/load
@app.post("/sample-datasets/load")
async def load_sample_datasets(request: SampleDatasetRequest):
    """
    Load sample documents from HuggingFace datasets.
    All datasets are freely available (no API key required).
    """
    from datasets import load_dataset
    
    loaded = []
    
    if "techqa" in request.datasets:
        ds = load_dataset("galileo-ai/ragbench", "techqa", split="test")
        samples = ds.select(range(min(request.samples_per_dataset, len(ds))))
        for sample in samples:
            await index_document(
                content=sample["documents"],
                metadata={"domain": "technical", "source": "techqa"},
                tenant_id=request.tenant_id
            )
        loaded.append({"dataset": "techqa", "count": len(samples)})
    
    if "hr_policies" in request.datasets:
        ds = load_dataset("syncora/hr-policies-qa-dataset", split="train")
        samples = ds.select(range(min(request.samples_per_dataset, len(ds))))
        for sample in samples:
            await index_document(
                content=sample["conversations"][-1]["value"],  # Assistant response
                metadata={"domain": "hr_policy", "source": "hr_policies"},
                tenant_id=request.tenant_id
            )
        loaded.append({"dataset": "hr_policies", "count": len(samples)})
    
    if "contracts" in request.datasets:
        ds = load_dataset("theatticusproject/cuad-qa", split="train")
        samples = ds.select(range(min(request.samples_per_dataset, len(ds))))
        for sample in samples:
            await index_document(
                content=sample["context"],
                metadata={"domain": "contracts", "source": "cuad"},
                tenant_id=request.tenant_id
            )
        loaded.append({"dataset": "contracts", "count": len(samples)})
    
    return {"loaded": loaded, "total": sum(d["count"] for d in loaded)}


class SampleDatasetRequest(BaseModel):
    datasets: List[str]  # ["techqa", "hr_policies", "contracts"]
    samples_per_dataset: int = 50
    tenant_id: str = "default"
```

**Available Datasets (all free, no API key):**

| Dataset | HuggingFace ID | Domain | License |
|---------|----------------|--------|---------|
| TechQA | `galileo-ai/ragbench` (techqa) | technical | CC-BY-4.0 |
| HR Policies | `syncora/hr-policies-qa-dataset` | hr_policy | Open |
| CUAD Contracts | `theatticusproject/cuad-qa` | contracts | CC-BY-4.0 |

**Web UI Component:**

```svelte
<!-- src/routes/documents/+page.svelte -->
<script lang="ts">
  let selectedDatasets = $state<string[]>([]);
  let samplesPerDataset = $state(50);
  let isLoading = $state(false);
  
  async function loadSampleData() {
    if (selectedDatasets.length === 0) return;
    isLoading = true;
    
    try {
      const response = await fetch(`${API_BASE}/api/v1/sample-datasets/load`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          datasets: selectedDatasets,
          samples_per_dataset: samplesPerDataset
        })
      });
      const result = await response.json();
      alert(`Loaded ${result.total} sample documents!`);
    } finally {
      isLoading = false;
    }
  }
</script>

<div class="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
  <h3 class="font-medium text-blue-900 mb-3">🚀 Quick Start: Load Sample Datasets</h3>
  <p class="text-sm text-blue-700 mb-4">
    Load real-world documents from HuggingFace to test the system.
  </p>
  
  <div class="space-y-2 mb-4">
    <label class="flex items-center gap-2">
      <input type="checkbox" bind:group={selectedDatasets} value="techqa" />
      <span>TechQA - IT Technical Support docs</span>
    </label>
    <label class="flex items-center gap-2">
      <input type="checkbox" bind:group={selectedDatasets} value="hr_policies" />
      <span>HR Policies - Employee handbook Q&A</span>
    </label>
    <label class="flex items-center gap-2">
      <input type="checkbox" bind:group={selectedDatasets} value="contracts" />
      <span>CUAD Contracts - Legal document clauses</span>
    </label>
  </div>
  
  <div class="flex items-center gap-4">
    <label class="text-sm">
      Samples per dataset:
      <input type="number" bind:value={samplesPerDataset} min="10" max="200" 
             class="w-20 px-2 py-1 border rounded ml-2" />
    </label>
    <button onclick={loadSampleData} disabled={isLoading || selectedDatasets.length === 0}
            class="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
      {isLoading ? 'Loading...' : 'Load Selected'}
    </button>
  </div>
</div>
```

---

### Feature: Document Upload with Domain Classification

Allow users to upload documents with optional domain label (auto-detect by default).

**API Endpoint (Document Service):**

```kotlin
// DocumentController.kt
@PostMapping("/documents", consumes = [MediaType.MULTIPART_FORM_DATA_VALUE])
suspend fun uploadDocument(
    @RequestPart("file") file: FilePart,
    @RequestPart("domain", required = false) domain: String?,
    @RequestHeader("X-Tenant-ID") tenantId: String
): DocumentResponse {
    
    val content = extractText(file)  // Apache Tika
    
    // Determine domain: use provided or auto-detect
    val documentDomain = if (domain.isNullOrBlank() || domain == "auto") {
        // Call RAG service to classify
        ragServiceClient.classifyDomain(content)
    } else {
        domain
    }
    
    // Store and index
    val document = documentRepository.save(
        Document(
            filename = file.filename(),
            contentType = file.headers().contentType?.toString(),
            domain = documentDomain,
            tenantId = tenantId
        )
    )
    
    // Trigger async indexing with domain metadata
    ragServiceClient.indexDocument(document.id, content, documentDomain)
    
    return document.toResponse()
}
```

**Domain Classification Endpoint (RAG Service):**

```python
# POST /classify-domain
@app.post("/classify-domain")
async def classify_domain(request: ClassifyRequest) -> ClassifyResponse:
    """
    Classify document content into a domain using zero-shot classification.
    Uses the same model as query routing for consistency.
    """
    from transformers import pipeline
    
    classifier = pipeline(
        "zero-shot-classification",
        model="MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33"
    )
    
    # Use first 1000 chars for classification (efficiency)
    text_sample = request.content[:1000]
    
    result = classifier(
        text_sample,
        candidate_labels=["hr_policy", "technical", "contracts", "general"],
        hypothesis_template="This document is about {}."
    )
    
    return ClassifyResponse(
        domain=result["labels"][0],
        confidence=result["scores"][0],
        all_scores=dict(zip(result["labels"], result["scores"]))
    )
```

**Web UI Upload Component:**

```svelte
<!-- src/lib/components/UploadModal.svelte -->
<script lang="ts">
  let file: File | null = $state(null);
  let domain = $state('auto');
  let isUploading = $state(false);
  
  const domains = [
    { value: 'auto', label: '🔍 Auto-detect (recommended)' },
    { value: 'technical', label: '💻 Technical Documentation' },
    { value: 'hr_policy', label: '📋 HR Policy' },
    { value: 'contracts', label: '⚖️ Legal Contract' },
    { value: 'general', label: '📄 General' }
  ];
  
  async function uploadDocument() {
    if (!file) return;
    isUploading = true;
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('domain', domain);
    
    try {
      const response = await fetch(`${API_BASE}/api/v1/documents`, {
        method: 'POST',
        body: formData
      });
      
      if (response.ok) {
        const doc = await response.json();
        alert(`Document uploaded! Detected domain: ${doc.domain}`);
      }
    } finally {
      isUploading = false;
    }
  }
</script>

<div class="p-6 bg-white rounded-lg shadow-lg max-w-md">
  <h2 class="text-lg font-semibold mb-4">Upload Document</h2>
  
  <!-- File Input -->
  <div class="mb-4">
    <label class="block text-sm font-medium mb-2">File</label>
    <input type="file" accept=".pdf,.docx,.txt,.md"
           onchange={(e) => file = e.target.files?.[0] ?? null}
           class="w-full border rounded p-2" />
  </div>
  
  <!-- Domain Selection -->
  <div class="mb-6">
    <label class="block text-sm font-medium mb-2">Document Domain</label>
    <div class="space-y-2">
      {#each domains as d}
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="radio" bind:group={domain} value={d.value} />
          <span class={domain === d.value ? 'font-medium' : ''}>{d.label}</span>
        </label>
      {/each}
    </div>
    <p class="text-xs text-gray-500 mt-2">
      Auto-detect uses AI to classify the document type automatically.
    </p>
  </div>
  
  <!-- Upload Button -->
  <button onclick={uploadDocument} 
          disabled={!file || isUploading}
          class="w-full py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
    {isUploading ? 'Uploading...' : 'Upload Document'}
  </button>
</div>
```

**Domain stored in metadata for filtered retrieval:**

```python
# When indexing, domain is stored in Qdrant payload
document = Document(
    content=content,
    meta={
        "document_id": doc_id,
        "domain": domain,  # "technical", "hr_policy", "contracts", "general"
        "tenant_id": tenant_id,
        "filename": filename
    }
)
```

---

### Component 8: Docker Compose

```yaml
# docker-compose.yml
# All images pinned to specific stable versions to prevent breaking changes
version: '3.8'

services:
  # === Application Services ===
  
  api-gateway:
    build: ./api-gateway
    ports:
      - "8080:8080"
    environment:
      - SPRING_PROFILES_ACTIVE=docker
    depends_on:
      - document-service
      - rag-service
      - admin-service
    networks:
      - docint-network

  document-service:
    build: ./document-service
    ports:
      - "8081:8081"
    environment:
      - SPRING_PROFILES_ACTIVE=docker
      - SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/docint
      - SPRING_DATASOURCE_USERNAME=docint
      - SPRING_DATASOURCE_PASSWORD=docint_secret
      - MINIO_ENDPOINT=http://minio:9000
      - MINIO_ACCESS_KEY=minioadmin
      - MINIO_SECRET_KEY=minioadmin
      - RAG_SERVICE_URL=http://rag-service:8000
    depends_on:
      postgres:
        condition: service_healthy
      minio:
        condition: service_started
    networks:
      - docint-network

  rag-service:
    build: ./rag-service
    ports:
      - "8000:8000"
    environment:
      - QDRANT_URL=http://qdrant:6333
      - OLLAMA_BASE_URL=http://ollama:11434
      # LLM: Qwen3-4B (Apache 2.0 license, ~5GB RAM, excellent quality)
      - LITELLM_MODEL=ollama/qwen3:4b
      - LITELLM_FALLBACKS=ollama/phi3:mini,ollama/qwen3:1.7b
      # Embedding: nomic-embed-text (Apache 2.0, 137M params)
      - EMBEDDING_MODEL=nomic-embed-text
      - RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
      - LANGFUSE_HOST=http://langfuse:3000
      - LANGFUSE_PUBLIC_KEY=pk-local
      - LANGFUSE_SECRET_KEY=sk-local
    depends_on:
      qdrant:
        condition: service_healthy
      ollama:
        condition: service_started
    volumes:
      - huggingface-cache:/root/.cache/huggingface
    networks:
      - docint-network
    deploy:
      resources:
        reservations:
          memory: 4G  # For embedding models

  admin-service:
    build: ./admin-service
    ports:
      - "8082:8082"
    environment:
      - SPRING_PROFILES_ACTIVE=docker
      - SPRING_DATASOURCE_URL=jdbc:postgresql://postgres:5432/docint
      - QDRANT_URL=http://qdrant:6333
      - REDIS_URL=redis://redis:6379
    depends_on:
      - postgres
      - qdrant
      - redis
    networks:
      - docint-network

  # === Data Layer ===
  # All images pinned to specific versions - never use :latest in production

  qdrant:
    image: qdrant/qdrant:v1.16.3
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant-data:/qdrant/storage
    environment:
      - QDRANT__SERVICE__GRPC_PORT=6334
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - docint-network

  postgres:
    image: pgvector/pgvector:0.8.0-pg17
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_DB=docint
      - POSTGRES_USER=docint
      - POSTGRES_PASSWORD=docint_secret
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./init-scripts:/docker-entrypoint-initdb.d
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U docint"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - docint-network

  redis:
    image: redis:7.4.7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis-data:/data
    command: redis-server --appendonly yes
    networks:
      - docint-network

  minio:
    image: minio/minio:RELEASE.2025-01-20T14-49-07Z
    ports:
      - "9000:9000"
      - "9001:9001"
    environment:
      - MINIO_ROOT_USER=minioadmin
      - MINIO_ROOT_PASSWORD=minioadmin
    volumes:
      - minio-data:/data
    command: server /data --console-address ":9001"
    networks:
      - docint-network

  # === AI Infrastructure ===

  # =============================================================================
  # Ollama: Use ollama-cpu for M1/Intel Macs, ollama-gpu for NVIDIA systems
  # Choose ONE profile when starting: docker compose --profile cpu up
  #                               or: docker compose --profile gpu up
  # =============================================================================
  
  ollama-cpu:
    image: ollama/ollama:0.5.7
    profiles: ["cpu"]  # Use: docker compose --profile cpu up
    ports:
      - "11434:11434"
    volumes:
      - ollama-data:/root/.ollama
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        /bin/ollama serve &
        sleep 5
        # All models Apache 2.0 licensed, commercial OK
        ollama pull qwen3:4b           # Primary LLM (~5GB)
        ollama pull phi3:mini          # Fallback LLM (~4.8GB)
        ollama pull nomic-embed-text   # Embedding (~500MB)
        echo "Models ready. Total: ~10GB. Fits M1 16GB with headroom."
        tail -f /dev/null
    networks:
      - docint-network
    # M1 Macs use unified memory - Ollama auto-detects Apple Silicon

  ollama-gpu:
    image: ollama/ollama:0.5.7
    profiles: ["gpu"]  # Use: docker compose --profile gpu up
    ports:
      - "11434:11434"
    volumes:
      - ollama-data:/root/.ollama
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        /bin/ollama serve &
        sleep 5
        # All models Apache 2.0 licensed, commercial OK
        ollama pull qwen3:4b           # Primary LLM (~5GB)
        ollama pull phi3:mini          # Fallback LLM (~4.8GB)
        ollama pull nomic-embed-text   # Embedding (~500MB)
        echo "Models ready with NVIDIA GPU acceleration."
        tail -f /dev/null
    networks:
      - docint-network
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

  langfuse:
    image: langfuse/langfuse:3.148.0
    ports:
      - "3000:3000"
    environment:
      - DATABASE_URL=postgresql://docint:docint_secret@postgres:5432/langfuse
      - NEXTAUTH_SECRET=mysecret
      - NEXTAUTH_URL=http://localhost:3000
      - SALT=mysalt
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - docint-network

networks:
  docint-network:
    driver: bridge

volumes:
  qdrant-data:
  postgres-data:
  redis-data:
  minio-data:
  ollama-data:
  huggingface-cache:
```

---

## Implementation Roadmap

### Phase 1: Infrastructure + Document Service

**Step 1-2: Docker Infrastructure**
- [ ] Create docker-compose.yml with all data services
- [ ] Verify Qdrant, PostgreSQL, Redis, MinIO start correctly
- [ ] Run Ollama and pull phi3:mini, nomic-embed-text models
- [ ] Set up Langfuse database
- [ ] Verify total RAM usage ~8-10GB (fits M1 16GB)

**Step 3-4: Document Service**
- [ ] Create Kotlin/Spring Boot project structure
- [ ] Implement document upload endpoint
- [ ] Integrate MinIO for file storage
- [ ] Implement Apache Tika text extraction

**Step 5: Chunking (Python/RAG Service)**
- [ ] Integrate Chonkie library for chunking
- [ ] Configure RecursiveChunker with 400 token chunks
- [ ] Store chunk metadata in PostgreSQL
- [ ] Write unit tests for chunking

### Phase 2: RAG Service Core

**Step 1-2: Embedding Pipeline**
- [ ] Create Python/FastAPI project structure
- [ ] Implement Haystack indexing pipeline
- [ ] Load nomic-embed-text model via Ollama (Apache 2.0, 768 dims)
- [ ] Test embedding storage in Qdrant

**Step 3-4: Query Pipeline**
- [ ] Implement hybrid search (dense + BM25)
- [ ] Add RRF fusion
- [ ] Integrate MiniLM reranker
- [ ] Implement LiteLLMGenerator component

**Step 5: End-to-End**
- [ ] Connect Document Service → RAG Service
- [ ] Test full flow: upload → chunk → embed → query
- [ ] Add basic error handling

### Phase 3: API Gateway + Multi-tenancy

**Step 1-2: API Gateway**
- [ ] Create gateway project with Spring Cloud Gateway
- [ ] Configure routing to backend services
- [ ] Add JWT validation (simple local key for dev)

**Step 3-4: Multi-tenancy + Access Control**
- [ ] Extract tenant_id from JWT in gateway
- [ ] Pass tenant_id to backend services
- [ ] Implement SecureRetriever with ACL filtering
- [ ] Add tenant + role filtering to Qdrant queries

**Step 5: Rate Limiting**
- [ ] Configure per-tenant rate limits
- [ ] Add Redis-based rate limiting
- [ ] Test rate limiting behavior

### Phase 4: Caching + Observability + Polish

**Step 1-2: Semantic Caching**
- [ ] Create response_cache collection in Qdrant
- [ ] Implement SemanticCacheChecker component
- [ ] Implement SemanticCacheWriter component
- [ ] Add cache hit/miss logging

**Step 3: Observability**
- [ ] Integrate Langfuse tracing
- [ ] Add @observe decorators to RAG functions
- [ ] Configure metrics endpoints
- [ ] Test trace visibility in Langfuse UI

**Step 4: Admin Service**
- [ ] Create admin service project
- [ ] Implement health check endpoint
- [ ] Implement cache clear endpoint
- [ ] Implement tenant stats endpoint

**Step 5: Evaluation + Documentation**
- [ ] Download RAGBench dataset from HuggingFace
- [ ] Run RAGAS evaluation
- [ ] Write API documentation
- [ ] Polish and bug fixes

---

## Evaluation Strategy

### Test Datasets: Enterprise Document Domains

Use curated HuggingFace datasets that match "Enterprise Document Intelligence" domains.
All are sized for M1 MacBook development.

```python
from datasets import load_dataset

# =============================================================================
# OPTION 1: TechQA (IBM Technical Support) - 1.8k examples
# Best for: IT documentation, Technotes, technical troubleshooting
# =============================================================================
techqa = load_dataset("galileo-ai/ragbench", "techqa")
# Split: train (~1.4k), test (~400)

# =============================================================================
# OPTION 2: HR Policies QA - 644 examples  
# Best for: HR policy documents, employee handbooks, compliance
# =============================================================================
hr_policies = load_dataset("syncora/hr-policies-qa-dataset")
# Contains: policy review cycles, anti-bribery, employee notifications

# =============================================================================
# OPTION 3: CUAD (Contract Understanding) - 22k+ examples
# Best for: Legal contracts, clause identification, contract review
# =============================================================================
cuad = load_dataset("theatticusproject/cuad-qa")
# Split: train (22,450), test (4,182)
# Use subset for development:
cuad_dev = cuad["train"].select(range(2000))

# =============================================================================
# COMBINED: Mix for comprehensive Enterprise DocIntel evaluation
# =============================================================================
# For balanced evaluation across enterprise document types:
combined_eval = {
    "technical": techqa["test"].select(range(200)),      # IT docs
    "hr_policy": hr_policies["train"].select(range(200)), # HR docs  
    "contracts": cuad["test"].select(range(200))          # Legal docs
}
# Total: 600 examples covering 3 enterprise domains
```

**Dataset Selection Guide:**
| Dataset | Size | Domain | Best For |
|---------|------|--------|----------|
| TechQA | 1.8k | IT Technical Support | Product docs, Technotes |
| HR Policies | 644 | HR/Compliance | Employee handbooks, policies |
| CUAD | 22k+ | Legal Contracts | Contract review, clause extraction |

**Recommended approach:**
1. Start with TechQA for initial development (most RAG-ready)
2. Add HR Policies for policy document testing
3. Use CUAD subset for contract understanding features

**Supplementary custom dataset (optional):**
For your specific documents, create 50-100 Q&A pairs:
- 60% simple factoid questions
- 30% multi-chunk synthesis
- 10% adversarial (no answer exists)

### Metrics

**Retrieval Quality (RAGAS):**
```python
from ragas import evaluate
from ragas.metrics import (
    context_precision,
    context_recall,
    faithfulness,
    answer_relevancy
)

results = evaluate(
    dataset=test_dataset,
    metrics=[
        context_precision,
        context_recall,
        faithfulness,
        answer_relevancy
    ]
)
```

Targets:
- Context Precision: > 0.6
- Context Recall: > 0.8
- Faithfulness: > 0.8
- Answer Relevancy: > 0.7

**System Metrics:**
- Query latency p50 < 2s, p95 < 5s
- Cache hit rate > 20% after warmup
- Document processing < 30s per page
- Error rate < 1%

---

## Phase: Multi-Tenancy RBAC & Platform Administration

**Status:** Planned (not yet implemented)

**Objective:** Proper role-based access control separating platform administrators from tenant users, with a dedicated management UI for platform operations.

### Roles

| Role | Scope | Examples |
|------|-------|---------|
| `platform_admin` | Full system access, all tenants | akadmin, ops team |
| `tenant_admin` | Full access within own tenant | demo-admin |
| `tenant_user` | Read/query within own tenant | demo-user |

### Implementation Plan

**1. Authentik Configuration**
- Add `platform-admin` group with custom claim `role: platform_admin`
- Add `tenant-admin` role as a group attribute
- Update scope mapping to include `role` claim in JWT
- `akadmin` and designated ops users → `platform-admin` group
- Tenant admins → respective tenant groups with `tenant_admin` attribute

**2. API Gateway — Role Extraction & Enforcement**
- Extend `TenantFilter` to extract `role` claim from JWT → `X-User-Role` header
- Add route-level authorization:
  - `/api/v1/admin/**` → requires `platform_admin` role
  - `/api/v1/tenants/**` → requires `platform_admin` role
  - `/api/v1/documents/all` (DELETE) → requires `platform_admin` or `tenant_admin` (own tenant only)
  - All other `/api/v1/**` → auto-scoped to user's `tenant_id` from JWT

**3. Backend Services — Tenant Scoping**
- Document Service: All queries auto-filtered by `tenant_id` from header; bulk delete validates role
- RAG Service: Conversations scoped by `tenant_id` + `user_id` from headers
- Admin Service: Stats endpoints return all-tenant data only for `platform_admin`; tenant-scoped data for others

**4. Web UI — Role-Aware Routing**
- Auth module: Expose `getRole()`, `isPlatformAdmin()`, `isTenantAdmin()` from JWT claims
- `/admin` route: Only rendered in nav and accessible for `platform_admin`
- Tenant admin panel: New `/settings` route for `tenant_admin` — manage own tenant's documents, users
- Documents page: Auto-scoped to user's tenant (no manual tenant_id selection)
- All API calls: Use `tenant_id` from JWT, not from UI state

**5. Platform Admin UI (new `/admin` route enhancements)**
- Cross-tenant document browser with tenant selector
- Tenant provisioning: Create/edit/delete tenants
- User management: View users per tenant, assign roles
- Audit log: Query history across tenants
- System config: Cache policies, rate limits per tenant

**6. Authentik Blueprint Updates**
- Add `platform-admin` group to `docintel-setup.yaml`
- Add role claim to scope mapping expression
- Configure `akadmin` as platform admin
- Add role-based authorization flow (optional: separate admin app in Authentik)

### Key Design Principles
- **JWT is the source of truth** — tenant_id and role come from the token, never from client input
- **Defense in depth** — Gateway enforces route-level auth, services enforce data-level scoping
- **Fail-closed** — Missing role/tenant → deny access
- **Audit everything** — All admin operations logged with actor identity

### Dependencies
- Authentik OAuth2 setup (done)
- oidc-client-ts integration (done)  
- Admin service endpoints (done)
- Admin UI page (done)

---

## Notes for AI-Assisted Coding

When using Cursor or Claude Code to implement these components:

1. **Start with data models** - Define the Kotlin/Python data classes first
2. **Then interfaces** - Define service interfaces before implementations
3. **Copy component specs** - Each component section above can be used as a prompt
4. **Test incrementally** - Get each component working before integration
5. **Use docker-compose** - Always develop against the full infrastructure

Example prompt for Cursor:
```
Implement the Document Service as specified:
- Kotlin/Spring Boot 3.2
- Endpoints: POST /internal/documents, GET /internal/documents/{id}
- Use Apache Tika for text extraction
- Store files in MinIO at documents/{tenant_id}/{document_id}/
- Store metadata in PostgreSQL
- Call RAG Service /embed after chunking

Data models:
[paste Document and Chunk entities]

Chunking service:
[paste ChunkingService interface]
```
