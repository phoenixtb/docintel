"""
RAG Service - FastAPI Application
=================================

Main entry point for the RAG service. Provides:
- Document indexing endpoint
- Query endpoint with domain-aware routing
- Streaming query endpoint (SSE)
- Health check endpoint
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional
import os
import asyncio
import json
import uuid
import httpx
from contextlib import asynccontextmanager

from ..pipelines import (
    get_query_pipeline,
    index_chunks,
    delete_document_vectors,
    delete_tenant_vectors,
)
from ..chunking import ChunkingService, ChunkingConfig, ChunkingMethod
from ..components import SemanticCacheWriter
from ..tracing import init_langfuse
from ..datasets import (
    get_domain_classifier,
    get_dataset_loader,
    DATASET_CONFIGS,
    DOMAIN_LABELS,
)


# =============================================================================
# Lifespan Management
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup, cleanup on shutdown."""
    print("RAG Service starting...")
    print(f"QDRANT_URL: {os.getenv('QDRANT_URL', 'not set')}")
    print(f"DOCUMENT_SERVICE_URL: {os.getenv('DOCUMENT_SERVICE_URL', 'not set')}")
    print(f"OLLAMA_BASE_URL: {os.getenv('OLLAMA_BASE_URL', 'not set')}")
    print(f"LITELLM_MODEL: {os.getenv('LITELLM_MODEL', 'ollama/qwen3:4b')}")

    # Initialize Langfuse tracing
    init_langfuse()

    # Ensure Qdrant collection exists
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
    
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=qdrant_url)
    
    # Create documents collection if it doesn't exist
    try:
        collection_info = client.get_collection("documents")
        print(f"Qdrant 'documents' collection exists with {collection_info.points_count} points")
    except Exception:
        print("Creating 'documents' collection in Qdrant...")
        client.create_collection(
            collection_name="documents",
            vectors_config=models.VectorParams(
                size=768,  # nomic-embed-text-v1.5 dimension
                distance=models.Distance.COSINE,
            ),
        )
        print("Created 'documents' collection")
    
    # Ensure payload indexes exist for filtering
    # Haystack stores metadata nested under 'meta' key
    try:
        client.create_payload_index(
            collection_name="documents",
            field_name="meta.tenant_id",  # Nested path
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        print("Created meta.tenant_id index")
    except Exception:
        pass  # Index may already exist
    
    try:
        client.create_payload_index(
            collection_name="documents",
            field_name="meta.document_type",  # Nested path
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        print("Created meta.document_type index")
    except Exception:
        pass

    # Pre-warm pipelines (lazy initialization will happen on first request)
    yield

    print("RAG Service shutting down...")


app = FastAPI(
    title="DocIntel RAG Service",
    description="Haystack-based RAG service for enterprise document Q&A",
    version="0.1.0",
    lifespan=lifespan,
)


# =============================================================================
# Request/Response Models
# =============================================================================

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    tenant_id: str = Field(default="default")
    user_roles: list[str] = Field(default_factory=list)
    user_id: Optional[str] = None
    document_type: Optional[str] = None  # Optional domain filter
    conversation_id: Optional[str] = None  # Link query to a conversation
    # top_k is now configured internally via RAG_DEFAULT_TOP_K env var
    # Kept as optional for admin/testing use only
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    use_cache: bool = True
    use_reranking: bool = True


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]
    cache_hit: bool
    latency_ms: int
    model_used: str


class ChunkData(BaseModel):
    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    metadata: dict = Field(default_factory=dict)


class IndexRequest(BaseModel):
    document_id: str
    tenant_id: str = Field(default="default")
    chunks: list[ChunkData]


class IndexResponse(BaseModel):
    status: str
    document_id: str
    embedded_count: int
    collection: str


class ChunkRequest(BaseModel):
    """Request for chunking text."""
    text: str
    document_id: str
    tenant_id: str = Field(default="default")
    filename: str = "unknown.txt"
    method: str = Field(default="recursive")  # recursive, semantic, token
    chunk_size: int = Field(default=400, ge=100, le=2000)
    chunk_overlap: int = Field(default=0, ge=0, le=200)
    metadata: dict = Field(default_factory=dict)


class ChunkResponse(BaseModel):
    document_id: str
    chunk_count: int
    chunks: list[dict]


class HealthResponse(BaseModel):
    status: str
    qdrant: str
    ollama: str
    version: str = "0.1.0"


# Domain Classification Models
class ClassifyDomainRequest(BaseModel):
    content: str = Field(..., min_length=10, max_length=50000)


class ClassifyDomainResponse(BaseModel):
    domain: str
    confidence: float
    all_scores: dict[str, float]


# Sample Dataset Models
class LoadSampleDatasetsRequest(BaseModel):
    datasets: list[str] = Field(..., min_items=1)
    samples_per_dataset: int = Field(
        default=100, 
        ge=1, 
        le=100000,
        description="Number of samples to load per dataset. Higher values take longer (embedding time)."
    )
    tenant_id: str = Field(default="default")


class LoadedDatasetInfo(BaseModel):
    dataset: str
    domain: str
    documents_loaded: int
    documents_indexed: int


class LoadSampleDatasetsResponse(BaseModel):
    loaded: list[LoadedDatasetInfo]
    total_documents: int
    total_indexed: int


# =============================================================================
# Health Check
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Check service and dependency health."""
    qdrant_status = "unknown"
    ollama_status = "unknown"

    # Check Qdrant
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))
        client.get_collections()
        qdrant_status = "connected"
    except Exception as e:
        qdrant_status = f"error: {str(e)[:50]}"

    # Check Ollama
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{os.getenv('OLLAMA_BASE_URL', 'http://localhost:11434')}/api/tags",
                timeout=5.0,
            )
            if response.status_code == 200:
                ollama_status = "connected"
            else:
                ollama_status = f"error: HTTP {response.status_code}"
    except Exception as e:
        ollama_status = f"error: {str(e)[:50]}"

    status = "healthy" if qdrant_status == "connected" else "degraded"

    return HealthResponse(
        status=status,
        qdrant=qdrant_status,
        ollama=ollama_status,
    )


# =============================================================================
# Vector Store Stats
# =============================================================================

class VectorStatsResponse(BaseModel):
    total_vectors: int
    collections: dict[str, int]
    tenant_stats: dict[str, int] = Field(default_factory=dict)


@app.get("/debug/sample-points")
async def debug_sample_points(limit: int = 5):
    """Debug endpoint to see sample points from Qdrant."""
    from qdrant_client import QdrantClient
    
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=qdrant_url)
    
    try:
        # Get some points without filter
        result = client.scroll(
            collection_name="documents",
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        
        points = []
        for point in result[0]:
            meta = point.payload.get("meta", {}) if point.payload else {}
            points.append({
                "id": str(point.id),
                "payload_keys": list(point.payload.keys()) if point.payload else [],
                "meta_keys": list(meta.keys()) if meta else [],
                "tenant_id": meta.get("tenant_id"),  # Now from nested meta
                "document_type": meta.get("document_type"),  # Now from nested meta
                "domain": meta.get("domain"),
                "content_preview": (point.payload.get("content", "")[:100] + "...") if point.payload else None,
            })
        
        return {"points": points, "total": len(points)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/vector-stats", response_model=VectorStatsResponse)
async def get_vector_stats(tenant_id: Optional[str] = None):
    """
    Get vector store statistics.
    
    Returns counts of vectors in Qdrant, useful for showing
    how many sample datasets have been loaded.
    """
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
    
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=qdrant_url)
    
    collections = {}
    total = 0
    tenant_stats = {}
    
    try:
        # Get all collections
        collection_list = client.get_collections().collections
        
        for coll in collection_list:
            info = client.get_collection(coll.name)
            count = info.points_count or 0
            collections[coll.name] = count
            total += count
        
        # Get per-domain stats for documents collection
        # Haystack stores metadata nested under 'meta' key
        if "documents" in collections:
            for domain in ["technical", "hr_policy", "contracts", "general"]:
                try:
                    result = client.count(
                        collection_name="documents",
                        count_filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="meta.document_type",  # Nested under meta
                                    match=models.MatchValue(value=domain),
                                ),
                            ]
                            + (
                                [
                                    models.FieldCondition(
                                        key="meta.tenant_id",  # Nested under meta
                                        match=models.MatchValue(value=tenant_id),
                                    )
                                ]
                                if tenant_id
                                else []
                            )
                        ),
                    )
                    if result.count > 0:
                        tenant_stats[domain] = result.count
                except:
                    pass
                    
    except Exception as e:
        print(f"Error getting vector stats: {e}")
    
    return VectorStatsResponse(
        total_vectors=total,
        collections=collections,
        tenant_stats=tenant_stats,
    )


# =============================================================================
# Query Endpoints
# =============================================================================

@app.post("/query", response_model=QueryResponse)
async def query_documents(request: QueryRequest):
    """
    Query documents with domain-aware routing.

    The pipeline:
    1. Embed query using nomic-embed-text
    2. Check semantic cache for similar queries
    3. Retrieve documents with tenant + ACL filters
    4. Rerank with MiniLM cross-encoder
    5. Generate answer via LiteLLM (Qwen3-4B or fallback)
    """
    try:
        pipeline = get_query_pipeline()

        result = pipeline.run(
            question=request.question,
            tenant_id=request.tenant_id,
            user_roles=request.user_roles,
            user_id=request.user_id,
            document_type=request.document_type,
            top_k=request.top_k,
        )

        return QueryResponse(
            answer=result["answer"],
            sources=result["sources"],
            cache_hit=result["cache_hit"],
            latency_ms=result["latency_ms"],
            model_used=result.get("model_used", "unknown"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/stream")
async def query_documents_stream(request: QueryRequest):
    """
    Query documents with streaming response (SSE).

    Returns Server-Sent Events with:
    - metadata: {query_id, detected_domain, cache_hit}
    - token: partial response tokens
    - sources: list of source documents
    - done: completion signal
    """
    async def generate():
        try:
            # Initialize components
            from haystack.components.embedders import SentenceTransformersTextEmbedder

            embedder = SentenceTransformersTextEmbedder(
                model=os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5"),
                trust_remote_code=True,
            )
            embedder.warm_up()

            # Get retriever
            from ..components import SecureRetriever, PromptBuilder

            retriever = SecureRetriever(
                qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            )
            prompt_builder = PromptBuilder()

            query_id = str(uuid.uuid4())

            # Send metadata
            yield f"data: {json.dumps({'metadata': {'query_id': query_id, 'cache_hit': False}})}\n\n"

            # Embed query
            embed_result = embedder.run(text=request.question)
            query_embedding = embed_result["embedding"]

            # Retrieve documents
            domain_filter = None
            if request.document_type and request.document_type != "all":
                domain_filter = {
                    "key": "document_type",
                    "match": {"value": request.document_type},
                }

            print(f"[DEBUG] Query: {request.question[:50]}...")
            print(f"[DEBUG] tenant_id: {request.tenant_id}, domain_filter: {domain_filter}")
            
            retrieval_result = retriever.run(
                query_embedding=query_embedding,
                tenant_id=request.tenant_id,
                user_roles=request.user_roles,
                user_id=request.user_id,
                domain_filter=domain_filter,
            )
            top_k = request.top_k or 5
            documents = retrieval_result["documents"][:top_k]
            
            print(f"[DEBUG] Retrieved {len(documents)} documents")
            if documents:
                print(f"[DEBUG] First doc meta: {documents[0].meta}")

            if not documents:
                # Import centralized prompts
                from src.prompts import NO_DOCUMENTS_RESPONSE, NO_RELEVANT_DOCUMENTS_RESPONSE
                
                # Check if we have ANY documents for this tenant
                from qdrant_client import QdrantClient
                qdrant = QdrantClient(url=os.getenv("QDRANT_URL", "http://localhost:6333"))
                
                try:
                    collection_info = qdrant.get_collection("documents")
                    has_documents = collection_info.points_count > 0
                except:
                    has_documents = False
                
                if not has_documents:
                    # No documents at all - guide user to upload
                    response = NO_DOCUMENTS_RESPONSE
                else:
                    # Documents exist but none matched the query
                    response = NO_RELEVANT_DOCUMENTS_RESPONSE.format(query=request.question)
                
                yield f"data: {json.dumps({'token': response})}\n\n"
                yield f"data: {json.dumps({'sources': [], 'done': True})}\n\n"
                return

            # Build prompt
            prompt_result = prompt_builder.run(documents=documents, query=request.question)
            prompt = prompt_result["prompt"]

            # Stream LLM response (OllamaChatGenerator with buffered streaming + JSON parse)
            import asyncio
            from haystack.dataclasses import ChatMessage
            from haystack_integrations.components.generators.ollama import OllamaChatGenerator
            from ..pipelines.query import _ollama_model_name, _build_section_label

            queue: asyncio.Queue = asyncio.Queue()

            async def streaming_callback(chunk):
                try:
                    content = getattr(chunk, "content", None)
                    if isinstance(content, str) and content:
                        queue.put_nowait(content)
                except Exception:
                    pass

            llm = OllamaChatGenerator(
                model=_ollama_model_name(os.getenv("LITELLM_MODEL", "ollama/qwen3:4b")),
                url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                generation_kwargs={"temperature": 0.1, "num_predict": 1024},
                streaming_callback=streaming_callback,
            )

            async def run_llm():
                try:
                    await llm.run_async(messages=[ChatMessage.from_user(prompt)])
                except Exception as e:
                    print(f"[ERROR] LLM run_async failed: {e}")
                finally:
                    queue.put_nowait(None)

            task = asyncio.create_task(run_llm())
            full_response = ""
            while True:
                item = await queue.get()
                if item is None:
                    break
                full_response += item
                yield f"data: {json.dumps({'token': item})}\n\n"
            await task

            answer = full_response.strip()

            # Build sources with ref_id and section for [1], [2] mapping
            sources = []
            for i, doc in enumerate(documents):
                chunk_idx = doc.meta.get("chunk_index", i)
                section = _build_section_label(doc.meta, chunk_idx)
                sources.append({
                    "ref_id": i + 1,
                    "document_id": doc.meta.get("document_id", ""),
                    "filename": doc.meta.get("filename", "Unknown"),
                    "section": section,
                    "chunk_index": chunk_idx,
                    "score": doc.score or 0.0,
                })

            # Persist messages to conversation if conversation_id provided
            if request.conversation_id:
                try:
                    from ..db import add_message
                    add_message(request.conversation_id, "user", request.question)
                    add_message(request.conversation_id, "assistant", answer, sources=sources)
                except Exception as persist_err:
                    print(f"Warning: Failed to persist messages: {persist_err}")

            yield f"data: {json.dumps({'sources': sources, 'done': True})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


# =============================================================================
# Indexing Endpoints
# =============================================================================

@app.post("/index", response_model=IndexResponse)
async def index_document(request: IndexRequest):
    """
    Index document chunks for retrieval.

    Called by Document Service after chunking.
    """
    try:
        chunks = [
            {
                "chunk_id": chunk.chunk_id,
                "content": chunk.content,
                "metadata": chunk.metadata,
            }
            for chunk in request.chunks
        ]

        result = await index_chunks(
            chunks=chunks,
            tenant_id=request.tenant_id,
            document_id=request.document_id,
        )

        return IndexResponse(
            status="indexed",
            document_id=request.document_id,
            embedded_count=result["embedded_count"],
            collection=result["collection"],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chunk", response_model=ChunkResponse)
async def chunk_text(request: ChunkRequest):
    """
    Chunk text using Chonkie library.

    Supports multiple chunking methods:
    - recursive: Best balance of quality and speed (default)
    - semantic: Embedding-based topic boundaries
    - token: Fixed token count
    """
    try:
        service = ChunkingService()

        # Map method string to enum
        method_map = {
            "recursive": ChunkingMethod.RECURSIVE,
            "semantic": ChunkingMethod.SEMANTIC,
            "token": ChunkingMethod.TOKEN,
        }
        method = method_map.get(request.method, ChunkingMethod.RECURSIVE)

        config = ChunkingConfig(
            method=method,
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
        )

        chunks = service.chunk_document(
            text=request.text,
            document_id=request.document_id,
            tenant_id=request.tenant_id,
            filename=request.filename,
            config=config,
            extra_metadata=request.metadata,
        )

        return ChunkResponse(
            document_id=request.document_id,
            chunk_count=len(chunks),
            chunks=[
                {
                    "chunk_id": str(uuid.uuid4()),
                    "content": chunk.content,
                    "start_char": chunk.start_char,
                    "end_char": chunk.end_char,
                    "token_count": chunk.token_count,
                    "metadata": chunk.metadata,
                }
                for chunk in chunks
            ],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/index/{tenant_id}/{document_id}")
async def delete_document(tenant_id: str, document_id: str):
    """Delete vectors for a specific document."""
    try:
        result = await delete_document_vectors(
            document_id=document_id,
            tenant_id=tenant_id,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/index/{tenant_id}")
async def delete_tenant(tenant_id: str):
    """Delete all vectors for a tenant."""
    try:
        result = await delete_tenant_vectors(tenant_id=tenant_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Root
# =============================================================================

@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "DocIntel RAG Service",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
        "endpoints": {
            "query": "POST /query",
            "query_stream": "POST /query/stream",
            "index": "POST /index",
            "chunk": "POST /chunk",
            "classify_domain": "POST /classify-domain",
            "load_datasets": "POST /sample-datasets/load",
            "available_datasets": "GET /sample-datasets",
            "delete_document": "DELETE /index/{tenant_id}/{document_id}",
            "delete_tenant": "DELETE /index/{tenant_id}",
        },
    }


# =============================================================================
# Domain Classification Endpoints
# =============================================================================

@app.post("/classify-domain", response_model=ClassifyDomainResponse)
async def classify_domain(request: ClassifyDomainRequest):
    """
    Classify text into a domain using zero-shot classification.

    Uses MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33 model.
    Labels: hr_policy, technical, contracts, general
    """
    try:
        classifier = get_domain_classifier()
        result = classifier.classify(request.content)

        return ClassifyDomainResponse(
            domain=result.domain,
            confidence=result.confidence,
            all_scores=result.all_scores,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Classification failed: {str(e)}")


# =============================================================================
# Sample Dataset Endpoints
# =============================================================================

@app.get("/sample-datasets")
async def list_sample_datasets():
    """
    List available sample datasets.

    Returns available datasets with their domains.
    """
    return {
        "available_datasets": [
            {
                "key": key,
                "name": config["name"],
                "domain": config["domain"],
                "description": f"Sample {config['domain']} documents",
            }
            for key, config in DATASET_CONFIGS.items()
        ],
        "domains": DOMAIN_LABELS,
    }


@app.post("/sample-datasets/load", response_model=LoadSampleDatasetsResponse)
async def load_sample_datasets(
    request: LoadSampleDatasetsRequest,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
):
    """
    Load sample documents from HuggingFace datasets and index them.

    This endpoint:
    1. Downloads samples from specified datasets
    2. Chunks the documents
    3. Indexes them for retrieval
    4. Creates document records in PostgreSQL (for document list)

    The tenant_id is extracted from:
    1. X-Tenant-Id header (from JWT via API Gateway) - preferred
    2. tenant_id in request body - fallback for dev mode

    Available datasets:
    - techqa: Technical documentation (domain: technical)
    - hr_policies: HR policy documents (domain: hr_policy)
    - cuad: Contract documents (domain: contracts)
    """
    # Use header tenant if available (from JWT), otherwise fall back to body
    tenant_id = x_tenant_id if x_tenant_id else request.tenant_id
    
    loader = get_dataset_loader()
    chunking_service = ChunkingService()

    loaded_info = []
    total_documents = 0
    total_indexed = 0
    
    # Track documents to create in PostgreSQL
    documents_to_create = []

    for dataset_key in request.datasets:
        if dataset_key not in DATASET_CONFIGS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown dataset: {dataset_key}. Valid: {list(DATASET_CONFIGS.keys())}",
            )

        try:
            # Load documents from HuggingFace
            documents = loader.load_dataset(
                dataset_key=dataset_key,
                samples=request.samples_per_dataset,
                tenant_id=tenant_id,
            )

            documents_loaded = len(documents)
            documents_indexed = 0

            # Process and index each document
            for idx, doc in enumerate(documents):
                doc_id = f"{dataset_key}_{idx}_{uuid.uuid4().hex[:8]}"
                filename = f"{dataset_key}_sample_{idx}.txt"

                # Chunk the document
                chunks = chunking_service.chunk_document(
                    text=doc.content,
                    document_id=doc_id,
                    tenant_id=tenant_id,
                    filename=filename,
                    extra_metadata={
                        "domain": doc.domain,
                        "document_type": doc.domain,
                        "source_dataset": doc.source_dataset,
                        **doc.metadata,
                    },
                )

                if chunks:
                    # Index the chunks
                    chunk_data = [
                        {
                            "chunk_id": str(uuid.uuid4()),
                            "content": chunk.content,
                            "metadata": chunk.metadata,
                        }
                        for chunk in chunks
                    ]

                    await index_chunks(
                        chunks=chunk_data,
                        tenant_id=tenant_id,
                        document_id=doc_id,
                    )
                    documents_indexed += 1
                    
                    # Track for PostgreSQL creation
                    documents_to_create.append({
                        "filename": filename,
                        "domain": doc.domain,
                        "chunkCount": len(chunks),
                        "metadata": {
                            "source_dataset": doc.source_dataset,
                            "sample_index": str(idx),
                        },
                        "content": doc.content[:500] if len(doc.content) > 500 else doc.content,
                    })

            loaded_info.append(LoadedDatasetInfo(
                dataset=dataset_key,
                domain=DATASET_CONFIGS[dataset_key]["domain"],
                documents_loaded=documents_loaded,
                documents_indexed=documents_indexed,
            ))

            total_documents += documents_loaded
            total_indexed += documents_indexed

        except Exception as e:
            loaded_info.append(LoadedDatasetInfo(
                dataset=dataset_key,
                domain=DATASET_CONFIGS[dataset_key].get("domain", "unknown"),
                documents_loaded=0,
                documents_indexed=0,
            ))
            print(f"Error loading dataset {dataset_key}: {e}")

    # Create document records in PostgreSQL via Document Service
    if documents_to_create:
        await _create_document_records(tenant_id, documents_to_create)

    return LoadSampleDatasetsResponse(
        loaded=loaded_info,
        total_documents=total_documents,
        total_indexed=total_indexed,
    )


# =============================================================================
# Conversation Endpoints (Chat History)
# =============================================================================

class CreateConversationRequest(BaseModel):
    tenant_id: str = Field(default="default")
    user_id: Optional[str] = None
    title: str = Field(default="New Conversation")


class UpdateConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)


@app.post("/conversations")
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    from ..db import create_conversation
    conv = create_conversation(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        title=request.title,
    )
    return conv


@app.get("/conversations")
async def list_conversations(
    tenant_id: str = "default",
    user_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """List conversations for a tenant/user, most recent first."""
    from ..db import list_conversations
    return list_conversations(tenant_id=tenant_id, user_id=user_id, limit=limit, offset=offset)


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, tenant_id: str = "default"):
    """Get a conversation with all its messages."""
    from ..db import get_conversation
    conv = get_conversation(conversation_id, tenant_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    tenant_id: str = "default",
):
    """Update a conversation's title."""
    from ..db import update_conversation_title
    conv = update_conversation_title(conversation_id, tenant_id, request.title)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str, tenant_id: str = "default"):
    """Delete a conversation and all its messages."""
    from ..db import delete_conversation
    deleted = delete_conversation(conversation_id, tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}


# =============================================================================
# Sample Dataset Helpers
# =============================================================================

async def _create_document_records(tenant_id: str, documents: list[dict]):
    """
    Create document records in PostgreSQL via Document Service.
    This syncs sample dataset entries with the document list UI.
    """
    document_service_url = os.getenv("DOCUMENT_SERVICE_URL", "http://document-service:8081")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{document_service_url}/internal/documents/bulk-create",
                json={
                    "tenantId": tenant_id,
                    "documents": documents,
                },
            )
            
            if response.status_code == 201:
                result = response.json()
                print(f"Created {result.get('created', 0)} document records in PostgreSQL for tenant {tenant_id}")
            else:
                print(f"Warning: Failed to create document records in PostgreSQL: {response.status_code} - {response.text}")
    except Exception as e:
        # Don't fail the whole operation if document record creation fails
        # The documents are still indexed in Qdrant and queryable
        print(f"Warning: Could not create document records in PostgreSQL: {e}")
