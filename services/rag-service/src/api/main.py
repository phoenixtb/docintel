"""
RAG Service — FastAPI Application
==================================

Entry point. All shared resources (Settings, RAGService, LangfuseTracer)
are initialised in the lifespan and stored on app.state. Endpoints inject
them via FastAPI Depends() from api/dependencies.py.
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..chunking import ChunkingConfig, ChunkingMethod, ChunkingService
from ..config import Settings
from ..context import _tenant_ctx, _role_ctx
from ..datasets import DATASET_CONFIGS, DOMAIN_LABELS, get_dataset_loader, get_domain_classifier
from ..pipelines import RAGService, delete_document_vectors, delete_tenant_vectors, index_chunks
from ..tracing import LangfuseTracer
from .dependencies import JWTClaimsDep, RAGServiceDep, SettingsDep, TracerDep

logger = logging.getLogger(__name__)


async def _emit_query_event(
    query_id: str,
    tenant_id: str,
    user_id: str,
    latency_ms: int,
    model_used: str,
    cache_hit: bool,
    source_count: int,
    analytics_url: str,
) -> None:
    """Fire-and-forget: POST query telemetry to analytics-service."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{analytics_url}/events/query",
                json={
                    "query_id": query_id,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "latency_ms": latency_ms,
                    "model_used": model_used,
                    "cache_hit": cache_hit,
                    "source_count": source_count,
                },
            )
    except Exception as e:
        logger.debug("Query telemetry emit failed (non-fatal): %s", e)


# =============================================================================
# Lifespan
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    app.state.settings = settings

    logger.info("RAG Service starting (version=%s)", settings.service_version)
    logger.info("QDRANT_URL=%s", settings.qdrant_url)
    logger.info("OLLAMA_BASE_URL=%s", settings.ollama_base_url)
    logger.info("OLLAMA_LLM_MODEL=%s", settings.ollama_llm_model)
    logger.info("OLLAMA_EMBED_MODEL=%s", settings.ollama_embed_model)

    # Tracing
    tracer = LangfuseTracer(settings)
    app.state.tracer = tracer

    # Ensure required Ollama models are available (pull if missing)
    await _ensure_ollama_models(settings)

    # Ensure Qdrant collection and payload indexes exist
    _ensure_qdrant_ready(settings)

    # RAGService (warm-up happens lazily on first query to avoid blocking startup)
    rag_service = RAGService(settings)
    app.state.rag_service = rag_service

    yield

    logger.info("RAG Service shutting down")
    tracer.shutdown()


async def _ensure_ollama_models(settings: Settings) -> None:
    """
    Verify required Ollama models are available; pull them if missing.
    Required models: LLM + embed. Reranker is CPU-local (not via Ollama).
    """
    required = [settings.ollama_llm_model, settings.ollama_embed_model]
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags", timeout=10.0)
            if r.status_code != 200:
                logger.warning("Ollama not reachable at %s — skipping model check", settings.ollama_base_url)
                return
            pulled = {m["name"].split(":")[0] for m in r.json().get("models", [])}
            for model in required:
                name = model.split(":")[0]
                if name not in pulled:
                    logger.info("Pulling Ollama model: %s", model)
                    await client.post(
                        f"{settings.ollama_base_url}/api/pull",
                        json={"name": model},
                        timeout=600.0,
                    )
                    logger.info("Pulled: %s", model)
                else:
                    logger.info("Ollama model ready: %s", model)
    except Exception as e:
        logger.warning("Ollama model check failed (non-fatal): %s", e)


def _ensure_qdrant_ready(settings: Settings) -> None:
    """
    Verify Qdrant is reachable at startup.

    Collections are now per-tenant (documents_{tenant_id}) and created lazily
    on first index for each tenant, or eagerly by admin-service on tenant creation.
    No single shared collection is created here.
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(url=settings.qdrant_url)
    try:
        client.get_collections()
        logger.info("Qdrant reachable at %s — per-tenant collections created on demand", settings.qdrant_url)
    except Exception as e:
        logger.error("Qdrant not reachable: %s", e)


app = FastAPI(
    title="DocIntel RAG Service",
    description="Haystack-based RAG service for enterprise document Q&A",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def tenant_context_middleware(request: Request, call_next):
    """Set tenant ContextVars from gateway-forwarded headers for every request."""
    token_t = _tenant_ctx.set(request.headers.get("X-Tenant-Id", "default"))
    token_r = _role_ctx.set(request.headers.get("X-User-Role", "tenant_user"))
    try:
        return await call_next(request)
    finally:
        _tenant_ctx.reset(token_t)
        _role_ctx.reset(token_r)


# Prometheus metrics — must be registered before first request (module level)
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics")
except ImportError:
    pass


# =============================================================================
# Request / Response models
# =============================================================================

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    tenant_id: str = Field(default="default")
    user_roles: list[str] = Field(default_factory=list)
    user_id: Optional[str] = None
    document_type: Optional[str] = None
    conversation_id: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    use_cache: bool = True
    use_reranking: bool = True


class QueryResponse(BaseModel):
    answer: str
    thinking: str = ""
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
    text: str
    document_id: str
    tenant_id: str = Field(default="default")
    filename: str = "unknown.txt"
    method: str = Field(default="recursive")
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


class VectorStatsResponse(BaseModel):
    total_vectors: int
    collections: dict[str, int]
    tenant_stats: dict[str, int] = Field(default_factory=dict)


class ClassifyDomainRequest(BaseModel):
    content: str = Field(..., min_length=10, max_length=50000)


class ClassifyDomainResponse(BaseModel):
    domain: str
    confidence: float
    all_scores: dict[str, float]


class LoadSampleDatasetsRequest(BaseModel):
    datasets: list[str] = Field(..., min_length=1)
    samples_per_dataset: int = Field(default=100, ge=1, le=100000)
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
# Health
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check(settings: SettingsDep):
    qdrant_status = "unknown"
    ollama_status = "unknown"

    try:
        from qdrant_client import QdrantClient
        QdrantClient(url=settings.qdrant_url).get_collections()
        qdrant_status = "connected"
    except Exception as e:
        qdrant_status = f"error: {str(e)[:50]}"

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags", timeout=5.0)
            ollama_status = "connected" if r.status_code == 200 else f"error: HTTP {r.status_code}"
    except Exception as e:
        ollama_status = f"error: {str(e)[:50]}"

    return HealthResponse(
        status="healthy" if qdrant_status == "connected" else "degraded",
        qdrant=qdrant_status,
        ollama=ollama_status,
    )


@app.get("/")
async def root():
    return {
        "service": "DocIntel RAG Service",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }


# =============================================================================
# Vector stats
# =============================================================================

@app.get("/vector-stats", response_model=VectorStatsResponse)
async def get_vector_stats(settings: SettingsDep, claims: JWTClaimsDep):
    from qdrant_client import QdrantClient
    from qdrant_client.http import models

    tenant_id = claims["tenant_id"]
    collection = f"documents_{tenant_id}"
    client = QdrantClient(url=settings.qdrant_url)
    collections: dict[str, int] = {}
    tenant_stats: dict[str, int] = {}

    try:
        # Per-tenant collection — no tenant_id filter needed; all vectors belong to this tenant
        total_result = client.count(collection_name=collection, exact=False)
        total = total_result.count
        collections[collection] = total

        for domain in ["technical", "hr_policy", "contracts", "general"]:
            try:
                result = client.count(
                    collection_name=collection,
                    count_filter=models.Filter(must=[
                        models.FieldCondition(
                            key="meta.document_type",
                            match=models.MatchValue(value=domain),
                        ),
                    ]),
                )
                if result.count > 0:
                    tenant_stats[domain] = result.count
            except Exception:
                pass
    except Exception as e:
        logger.warning("Error getting vector stats for tenant %s (collection may not exist): %s", tenant_id, e)

    return VectorStatsResponse(
        total_vectors=sum(collections.values()),
        collections=collections,
        tenant_stats=tenant_stats,
    )



# =============================================================================
# Query endpoints
# =============================================================================

@app.post("/query", response_model=QueryResponse)
async def query_documents(
    request: QueryRequest,
    rag_service: RAGServiceDep,
    claims: JWTClaimsDep,
    settings: SettingsDep,
):
    """
    RAG query with tenant isolation and RBAC.
    Gateway claims always take precedence — clients cannot override tenant_id.
    """
    tenant_id = claims["tenant_id"]
    user_roles = claims["user_roles"] or request.user_roles
    user_id = claims["user_id"] or request.user_id

    try:
        result = rag_service.query(
            question=request.question,
            tenant_id=tenant_id,
            user_roles=user_roles or None,
            user_id=user_id,
            document_type=request.document_type,
            top_k=request.top_k,
            conversation_id=request.conversation_id,
            min_score=request.min_score,
        )
        asyncio.create_task(_emit_query_event(
            query_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            user_id=user_id or "",
            latency_ms=result["latency_ms"],
            model_used=result.get("model_used", "unknown"),
            cache_hit=result["cache_hit"],
            source_count=len(result["sources"]),
            analytics_url=settings.analytics_service_url,
        ))
        return QueryResponse(
            answer=result["answer"],
            thinking=result.get("thinking", ""),
            sources=result["sources"],
            cache_hit=result["cache_hit"],
            latency_ms=result["latency_ms"],
            model_used=result.get("model_used", "unknown"),
        )
    except Exception as e:
        logger.exception("Query failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/stream")
async def query_documents_stream(
    request: QueryRequest,
    rag_service: RAGServiceDep,
    claims: JWTClaimsDep,
    settings: SettingsDep,
):
    """
    RAG query with streaming SSE response.

    Runs embedding + retrieval via the RAGService components,
    then streams the LLM response token-by-token.
    """
    import asyncio

    from haystack_integrations.components.generators.ollama import OllamaChatGenerator

    from ..pipelines.query import _build_section_label

    tenant_id = claims["tenant_id"]
    user_roles = claims["user_roles"] or request.user_roles
    user_id = claims["user_id"] or request.user_id

    async def generate():
        try:
            # Ensure RAGService is warmed up (embedders available)
            if not rag_service._ready:
                rag_service.warm_up()

            query_id = str(uuid.uuid4())
            yield f"data: {json.dumps({'metadata': {'query_id': query_id, 'cache_hit': False}})}\n\n"

            # Embed
            embed_result = rag_service._dense_embedder.run(text=request.question)
            query_embedding = embed_result["embedding"]
            sparse_result = rag_service._sparse_embedder.run(text=request.question)
            query_sparse_embedding = sparse_result.get("sparse_embedding")

            # ── Query Routing (Pattern 1 from production-rag-concepts) ──────
            # If the caller didn't specify a domain, auto-classify the query
            # so retrieval stays within the relevant knowledge-base partition.
            # Confidence threshold: only apply the filter when the classifier
            # is reasonably sure — below it we fall back to unfiltered search.
            ROUTING_CONFIDENCE_THRESHOLD = 0.55
            routed_domain: str | None = None

            if request.document_type and request.document_type != "all":
                # Explicit caller override — honour it directly.
                routed_domain = request.document_type
            else:
                try:
                    from ..datasets import get_domain_classifier
                    clf = get_domain_classifier()
                    result = clf.classify(request.question)
                    if result.confidence >= ROUTING_CONFIDENCE_THRESHOLD:
                        routed_domain = result.domain
                        logger.info(
                            "Query routed to domain '%s' (confidence=%.2f)",
                            routed_domain, result.confidence,
                        )
                    else:
                        logger.info(
                            "Query routing confidence too low (%.2f) — searching all domains",
                            result.confidence,
                        )
                except Exception as e:
                    logger.warning("Domain classifier failed (non-fatal): %s", e)

            domain_filter = None
            if routed_domain:
                domain_filter = {"key": "document_type", "match": {"value": routed_domain}}

            # Re-emit metadata with routing info so the UI can show which domain was used
            yield f"data: {json.dumps({'routing': {'domain': routed_domain, 'explicit': bool(request.document_type and request.document_type != 'all')}})}\n\n"

            retrieval_result = rag_service._pipeline.get_component("retriever").run(  # type: ignore[union-attr]
                query_embedding=query_embedding,
                query_sparse_embedding=query_sparse_embedding,
                tenant_id=tenant_id,
                user_roles=user_roles or None,
                user_id=user_id,
                domain_filter=domain_filter,
            )
            top_k = request.top_k or settings.rag_default_top_k
            documents = retrieval_result["documents"][:top_k]

            logger.info(
                "Retrieved %d documents (domain_filter=%s)",
                len(documents), routed_domain or "none",
            )

            if not documents:
                from ..prompts import NO_DOCUMENTS_RESPONSE
                yield f"data: {json.dumps({'token': NO_DOCUMENTS_RESPONSE})}\n\n"
                yield f"data: {json.dumps({'sources': [], 'done': True})}\n\n"
                return

            # Prompt
            prompt_result = rag_service._pipeline.get_component("prompt_builder").run(
                documents=documents, query=request.question
            )
            messages: list[ChatMessage] = prompt_result["messages"]

            # Stream LLM via Haystack's OllamaChatGenerator.
            # ollama-haystack ≥6.1 maps message.thinking → chunk.reasoning.reasoning_text
            # and message.content → chunk.content, giving clean separation without
            # any custom parsing or direct Ollama API calls.
            queue: asyncio.Queue = asyncio.Queue()

            full_thinking = ""
            full_answer = ""

            async def streaming_callback(chunk):
                # chunk.reasoning is ReasoningContent during thinking phase; None otherwise.
                if chunk.reasoning and chunk.reasoning.reasoning_text:
                    queue.put_nowait(("thinking", chunk.reasoning.reasoning_text))
                elif chunk.content:
                    queue.put_nowait(("answer", chunk.content))

            llm = OllamaChatGenerator(
                model=settings.ollama_llm_model,
                url=settings.ollama_base_url,
                think=True,
                generation_kwargs={
                    "temperature": settings.ollama_llm_temperature,
                    "num_predict": settings.ollama_llm_max_tokens,
                },
                streaming_callback=streaming_callback,
            )

            async def run_llm():
                try:
                    await llm.run_async(messages=messages)
                except Exception as e:
                    logger.error("Streaming LLM failed: %s", e)
                finally:
                    queue.put_nowait(None)

            task = asyncio.create_task(run_llm())

            while True:
                item = await queue.get()
                if item is None:
                    break
                kind, text = item
                if kind == "thinking":
                    full_thinking += text
                    yield f"data: {json.dumps({'thinking_token': text})}\n\n"
                else:
                    full_answer += text
                    yield f"data: {json.dumps({'token': text})}\n\n"

            await task

            answer = full_answer.strip()

            sources = []
            for i, doc in enumerate(documents):
                chunk_idx = doc.meta.get("chunk_index", i)
                sources.append({
                    "ref_id": i + 1,
                    "document_id": doc.meta.get("document_id", ""),
                    "filename": doc.meta.get("filename", "Unknown"),
                    "section": _build_section_label(doc.meta, chunk_idx),
                    "chunk_index": chunk_idx,
                    "score": doc.score or 0.0,
                    "content": (doc.content or "")[:600],
                    "domain": doc.meta.get("document_type") or doc.meta.get("domain") or "",
                })

            if request.conversation_id:
                try:
                    from ..db import add_message
                    add_message(request.conversation_id, "user", request.question, tenant_id=tenant_id)
                    add_message(request.conversation_id, "assistant", answer, tenant_id=tenant_id, sources=sources)
                except Exception as e:
                    logger.warning("Failed to persist streaming conversation: %s", e)

            # Fire-and-forget query telemetry to analytics-service.
            asyncio.create_task(_emit_query_event(
                query_id=query_id,
                tenant_id=tenant_id,
                user_id=user_id or "",
                latency_ms=0,
                model_used=settings.ollama_llm_model,
                cache_hit=False,
                source_count=len(sources),
                analytics_url=settings.analytics_service_url,
            ))

            yield f"data: {json.dumps({'sources': sources, 'done': True})}\n\n"

        except Exception as e:
            logger.exception("Streaming query failed")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# =============================================================================
# Indexing endpoints
# =============================================================================

@app.post("/index", response_model=IndexResponse)
async def index_document(request: IndexRequest, settings: SettingsDep, claims: JWTClaimsDep):
    """Index document chunks into the tenant's Qdrant collection.
    Tenant ID is always taken from gateway claims — request body tenant_id is ignored."""
    try:
        tenant_id = claims["tenant_id"]
        chunks = [
            {"chunk_id": c.chunk_id, "content": c.content, "metadata": c.metadata}
            for c in request.chunks
        ]
        result = await index_chunks(
            chunks=chunks,
            tenant_id=tenant_id,
            document_id=request.document_id,
            settings=settings,
        )
        return IndexResponse(
            status="indexed",
            document_id=request.document_id,
            embedded_count=result["embedded_count"],
            collection=result["collection"],
        )
    except Exception as e:
        logger.exception("Indexing failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chunk", response_model=ChunkResponse)
async def chunk_text(request: ChunkRequest, claims: JWTClaimsDep):
    """Chunk document text. Tenant ID from gateway claims is authoritative."""
    try:
        tenant_id = claims["tenant_id"]
        service = ChunkingService()
        method_map = {
            "recursive": ChunkingMethod.RECURSIVE,
            "semantic": ChunkingMethod.SEMANTIC,
            "token": ChunkingMethod.TOKEN,
        }
        config = ChunkingConfig(
            method=method_map.get(request.method, ChunkingMethod.RECURSIVE),
            chunk_size=request.chunk_size,
            chunk_overlap=request.chunk_overlap,
        )
        chunks = service.chunk_document(
            text=request.text,
            document_id=request.document_id,
            tenant_id=tenant_id,
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
        logger.exception("Chunking failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/index/{tenant_id}/{document_id}")
async def delete_document(tenant_id: str, document_id: str, settings: SettingsDep):
    try:
        return await delete_document_vectors(document_id=document_id, tenant_id=tenant_id, settings=settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/index/{tenant_id}")
async def delete_tenant(tenant_id: str, settings: SettingsDep):
    try:
        return await delete_tenant_vectors(tenant_id=tenant_id, settings=settings)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Domain classification
# =============================================================================

@app.post("/classify-domain", response_model=ClassifyDomainResponse)
async def classify_domain(request: ClassifyDomainRequest):
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
# Sample datasets
# =============================================================================

@app.get("/sample-datasets")
async def list_sample_datasets():
    return {
        "available_datasets": [
            {
                "key": key,
                "name": cfg["name"],
                "domain": cfg["domain"],
                "description": f"Sample {cfg['domain']} documents",
            }
            for key, cfg in DATASET_CONFIGS.items()
        ],
        "domains": DOMAIN_LABELS,
    }


@app.post("/sample-datasets/load", response_model=LoadSampleDatasetsResponse)
async def load_sample_datasets(
    request: LoadSampleDatasetsRequest,
    settings: SettingsDep,
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-Id"),
):
    tenant_id = x_tenant_id or request.tenant_id
    loader = get_dataset_loader()
    chunking_service = ChunkingService()

    loaded_info: list[LoadedDatasetInfo] = []
    total_documents = 0
    total_indexed = 0
    documents_to_create: list[dict] = []

    for dataset_key in request.datasets:
        if dataset_key not in DATASET_CONFIGS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown dataset: {dataset_key}. Valid: {list(DATASET_CONFIGS.keys())}",
            )
        try:
            documents = loader.load_dataset(
                dataset_key=dataset_key,
                samples=request.samples_per_dataset,
                tenant_id=tenant_id,
            )
            documents_indexed = 0

            for idx, doc in enumerate(documents):
                doc_id = f"{dataset_key}_{idx}_{uuid.uuid4().hex[:8]}"
                filename = f"{dataset_key}_sample_{idx}.txt"
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
                    await index_chunks(
                        chunks=[
                            {"chunk_id": str(uuid.uuid4()), "content": c.content, "metadata": c.metadata}
                            for c in chunks
                        ],
                        tenant_id=tenant_id,
                        document_id=doc_id,
                        settings=settings,
                    )
                    documents_indexed += 1
                    documents_to_create.append({
                        "filename": filename,
                        "domain": doc.domain,
                        "chunkCount": len(chunks),
                        "metadata": {"source_dataset": doc.source_dataset, "sample_index": str(idx)},
                        "content": doc.content[:500],
                    })

            loaded_info.append(LoadedDatasetInfo(
                dataset=dataset_key,
                domain=DATASET_CONFIGS[dataset_key]["domain"],
                documents_loaded=len(documents),
                documents_indexed=documents_indexed,
            ))
            total_documents += len(documents)
            total_indexed += documents_indexed

        except Exception as e:
            logger.error("Error loading dataset %s: %s", dataset_key, e)
            loaded_info.append(LoadedDatasetInfo(
                dataset=dataset_key,
                domain=DATASET_CONFIGS[dataset_key].get("domain", "unknown"),
                documents_loaded=0,
                documents_indexed=0,
            ))

    if documents_to_create:
        await _create_document_records(tenant_id, documents_to_create, settings)

    return LoadSampleDatasetsResponse(
        loaded=loaded_info,
        total_documents=total_documents,
        total_indexed=total_indexed,
    )


async def _create_document_records(tenant_id: str, documents: list[dict], settings: Settings) -> None:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.document_service_url}/internal/documents/bulk-create",
                json={"tenantId": tenant_id, "documents": documents},
            )
            if response.status_code == 201:
                result = response.json()
                logger.info("Created %s document records for tenant %s", result.get("created", 0), tenant_id)
            else:
                logger.warning(
                    "Failed to create document records: %s - %s",
                    response.status_code,
                    response.text[:200],
                )
    except Exception as e:
        logger.warning("Could not create document records in PostgreSQL: %s", e)


# =============================================================================
# Conversation endpoints
# =============================================================================

class CreateConversationRequest(BaseModel):
    tenant_id: str = Field(default="default")
    user_id: Optional[str] = None
    title: str = Field(default="New Conversation")


class UpdateConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)


@app.post("/conversations")
async def create_conversation(request: CreateConversationRequest, claims: JWTClaimsDep):
    from ..db import create_conversation as _create
    tenant_id = claims["tenant_id"]
    return _create(tenant_id=tenant_id, user_id=claims["user_id"] or request.user_id, title=request.title)


@app.get("/conversations")
async def list_conversations(
    claims: JWTClaimsDep,
    limit: int = 50,
    offset: int = 0,
):
    from ..db import list_conversations as _list
    return _list(tenant_id=claims["tenant_id"], user_id=claims["user_id"], limit=limit, offset=offset)


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, claims: JWTClaimsDep):
    from ..db import get_conversation as _get
    conv = _get(conversation_id, claims["tenant_id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    claims: JWTClaimsDep,
):
    from ..db import update_conversation_title
    conv = update_conversation_title(conversation_id, claims["tenant_id"], request.title)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str, claims: JWTClaimsDep):
    from ..db import delete_conversation
    if not delete_conversation(conversation_id, claims["tenant_id"]):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}
