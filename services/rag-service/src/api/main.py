"""
RAG Service — FastAPI Application
==================================

Entry point. All shared resources (Settings, RAGService, LangfuseTracer)
are initialised in the lifespan and stored on app.state. Endpoints inject
them via FastAPI Depends() from api/dependencies.py.
"""

import asyncio
import contextvars
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Optional

from docintel_common.tracing import TraceContext, configure_trace_logging

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..components.model_resolver import TenantModelResolver
from ..config import Settings
from ..context import _tenant_ctx, _role_ctx
from ..pipelines import RAGService
from ..tracing import LangfuseTracer
from .dependencies import RAGServiceDep, SettingsDep, TracerDep, UserContextDep

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


def _fire_and_forget(coro) -> asyncio.Task:
    """Create a task and attach an error-logging callback so exceptions are never silently dropped."""
    task = asyncio.ensure_future(coro)

    def _on_done(t: asyncio.Task):
        if not t.cancelled() and t.exception():
            logger.warning("Background task raised an exception: %s", t.exception())

    task.add_done_callback(_on_done)
    return task


async def _run_db(fn: Callable):
    """Run a synchronous DB call in the default executor, preserving contextvars for RLS."""
    ctx = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, ctx.run, fn)


# =============================================================================
# Lifespan
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_trace_logging()
    settings = Settings()
    app.state.settings = settings

    logger.info("RAG Service starting (version=%s)", settings.service_version)
    logger.info("QDRANT_URL=%s", settings.qdrant_url)
    logger.info("LLM_CHAT_URL=%s", settings.llm_chat_url)
    logger.info("LLM_EMBED_URL=%s", settings.llm_embed_url)
    logger.info("LLM_MODEL=%s", settings.llm_model)
    logger.info("LLM_EMBED_MODEL=%s", settings.llm_embed_model)

    # Tracing
    tracer = LangfuseTracer(settings)
    app.state.tracer = tracer

    # Probe LLM engines — log reachability and available models (non-fatal)
    await _probe_llm_engine(settings)

    # Trigger chat model pre-load in LMForge/Ollama (fire-and-forget).
    # Local engines load models lazily on first request (can take 2-5 s).
    # Sending a tiny request now ensures the model is hot before users query.
    asyncio.ensure_future(_prewarm_chat_model(settings))

    # Ensure Qdrant collection and payload indexes exist
    _ensure_qdrant_ready(settings)

    # RAGService — create now; warm up in background so the reranker JIT
    # compilation (can be 60-120 s on CPU) finishes before the first user query.
    rag_service = RAGService(settings)
    app.state.rag_service = rag_service
    asyncio.ensure_future(_prewarm_rag_service(rag_service))

    # Model resolver — resolves effective LLM per tenant from PostgreSQL (60s TTL cache)
    app.state.model_resolver = TenantModelResolver(
        postgres_url=settings.postgres_url,
        default_model=settings.llm_model,
    )

    # Anchored iterative summarizer — compresses evicted conversation turns using the
    # fast expansion model. Runs async fire-and-forget after each conversation persist.
    from ..components.summarizer import AnchoredSummarizer
    app.state.summarizer = AnchoredSummarizer(
        llm_chat_url=settings.llm_chat_url,
        model=settings.llm_expansion_model,
        api_key=settings.llm_api_key,
    )
    logger.info("AnchoredSummarizer ready (model=%s)", settings.llm_expansion_model)

    # LLM concurrency gate — configurable via LLM_CONCURRENCY_LIMIT env var.
    # Local serial engines (LMForge, Ollama): 2-4. Hosted APIs: 20-50+. vLLM: gpu_count * 4.
    app.state.llm_semaphore = asyncio.Semaphore(settings.llm_concurrency_limit)
    logger.info("LLM concurrency limit: %d", settings.llm_concurrency_limit)

    yield

    logger.info("RAG Service shutting down")
    tracer.shutdown()


async def _probe_llm_engine(settings: Settings) -> None:
    """
    Probe chat and embed engine endpoints at startup.

    Non-fatal — a missing engine only degrades functionality; it does not
    prevent the service from starting (engines may start after rag-service).
    Model pull is the responsibility of the engine-specific setup script.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        for label, url in [
            ("chat", settings.llm_chat_url),
            ("embed", settings.llm_embed_url),
        ]:
            try:
                r = await client.get(f"{url}/models")
                if r.status_code == 200:
                    model_ids = [m.get("id", "") for m in r.json().get("data", [])]
                    logger.info("LLM engine [%s] ready at %s — models: %s", label, url, model_ids)
                else:
                    logger.warning("LLM engine [%s] at %s returned HTTP %s", label, url, r.status_code)
            except Exception as e:
                logger.warning("LLM engine [%s] not reachable at %s (non-fatal): %s", label, url, e)


async def _prewarm_rag_service(rag_service) -> None:
    """
    Run RAGService.warm_up() in a thread so the cross-encoder JIT compilation
    (60-120 s on CPU) happens during startup instead of blocking the first query.
    """
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, rag_service.warm_up)
        logger.info("RAG pipeline warm-up complete (reranker ready)")
    except Exception as e:
        logger.warning("RAG pipeline warm-up failed (non-fatal): %s", e)


async def _prewarm_chat_model(settings: Settings) -> None:
    """
    Fire a minimal non-streaming chat request at startup so the local LLM
    engine (LMForge/Ollama) loads the model weights before the first real query.
    Non-fatal — failures are logged and ignored.
    """
    await asyncio.sleep(2)  # give engine a moment after probe
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            payload = {
                "model": settings.llm_model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
                "stream": False,
            }
            r = await client.post(f"{settings.llm_chat_url}/chat/completions", json=payload)
            if r.status_code == 200:
                logger.info("Chat model pre-warm OK (model=%s)", settings.llm_model)
            else:
                logger.warning("Chat model pre-warm returned HTTP %s", r.status_code)
    except Exception as e:
        logger.warning("Chat model pre-warm failed (non-fatal): %s", e)


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
    """Set tenant ContextVars and distributed trace context for every request."""
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    tenant_id  = request.headers.get("X-Tenant-Id", "default")
    user_id    = request.headers.get("X-User-Id", "")

    TraceContext.set(request_id, tenant_id, user_id)

    token_t = _tenant_ctx.set(tenant_id)
    token_r = _role_ctx.set(request.headers.get("X-User-Role", "tenant_user"))

    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        _tenant_ctx.reset(token_t)
        _role_ctx.reset(token_r)


# Prometheus metrics — must be registered before first request (module level)
try:
    from prometheus_client import Counter, Gauge, Histogram
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics")

    # Custom RAG metrics
    RAG_QUERY_LATENCY = Histogram(
        "rag_query_latency_seconds",
        "End-to-end RAG query latency",
        ["tenant", "cache_hit"],
        buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
    )
    RAG_CACHE_HITS = Counter("rag_cache_hit_total", "Semantic cache hits", ["tenant"])
    RAG_CACHE_MISSES = Counter("rag_cache_miss_total", "Semantic cache misses", ["tenant"])
    RAG_LLM_QUEUE_WAITING = Gauge("rag_llm_queue_waiting", "Current semaphore waiters")
    RAG_CHUNKS_INDEXED = Counter("rag_indexing_chunks_total", "Total chunks indexed", ["tenant"])
    _METRICS_ENABLED = True
except ImportError:
    _METRICS_ENABLED = False


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
    # None = use tenant preference; true/false = per-query override
    thinking_mode: Optional[bool] = None


class QueryResponse(BaseModel):
    answer: str
    thinking: str = ""
    sources: list[dict]
    cache_hit: bool
    latency_ms: int
    model_used: str


class HealthResponse(BaseModel):
    status: str
    qdrant: str
    llm_engine: str
    version: str = "0.1.0"


# Model families known to support the Ollama thinking/reasoning API.
# Checked via substring match against the lowercased model name.
_THINKING_MODEL_FAMILIES = {"qwen3", "qwq", "deepseek-r1", "marco-o1", "skywork-o1", ":r1"}


def _model_supports_thinking(model_name: str) -> bool:
    name_lower = model_name.lower()
    return any(family in name_lower for family in _THINKING_MODEL_FAMILIES)


class ModelInfo(BaseModel):
    name: str
    size: Optional[int] = None
    modified_at: Optional[str] = None
    supports_thinking: bool = False


class ModelListResponse(BaseModel):
    models: list[ModelInfo]
    default_model: str
    tenant_thinking_mode: bool = False


class VectorStatsResponse(BaseModel):
    total_vectors: int
    collections: dict[str, int]
    tenant_stats: dict[str, int] = Field(default_factory=dict)


# =============================================================================
# Health
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check(settings: SettingsDep):
    qdrant_status = "unknown"
    llm_status = "unknown"

    try:
        from qdrant_client import QdrantClient
        QdrantClient(url=settings.qdrant_url).get_collections()
        qdrant_status = "connected"
    except Exception as e:
        qdrant_status = f"error: {str(e)[:50]}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.llm_chat_url}/models")
            llm_status = "connected" if r.status_code == 200 else f"error: HTTP {r.status_code}"
    except Exception as e:
        llm_status = f"error: {str(e)[:50]}"

    return HealthResponse(
        status="healthy" if qdrant_status == "connected" else "degraded",
        qdrant=qdrant_status,
        llm_engine=llm_status,
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
# Model list (for dynamic model selection UI)
# =============================================================================

@app.get("/models", response_model=ModelListResponse)
async def list_models(
    settings: SettingsDep,
    http_request: Request,
    x_tenant_id: Optional[str] = Header(None),
):
    """
    List all chat models available from the configured LLM engine.
    Uses the standard GET /v1/models endpoint (OpenAI-compatible).
    Enriches each model with supports_thinking and returns the tenant's thinking_mode preference.
    """
    _EMBED_KEYWORDS = {"embed", "nomic", "mxbai", "bge", "gte", "e5-"}

    models: list[ModelInfo] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{settings.llm_chat_url}/models")
            if r.status_code == 200:
                raw_models = r.json().get("data", [])
                for m in raw_models:
                    name: str = m.get("id", "") or m.get("name", "")
                    name_lower = name.lower()
                    if not name or any(kw in name_lower for kw in _EMBED_KEYWORDS):
                        continue
                    # LMForge exposes capabilities.thinking — use it when available
                    capabilities: dict = m.get("capabilities", {})
                    supports_thinking = capabilities.get("thinking", _model_supports_thinking(name))
                    models.append(ModelInfo(
                        name=name,
                        size=m.get("size"),
                        modified_at=m.get("modified_at") or m.get("created"),
                        supports_thinking=supports_thinking,
                    ))
    except Exception as exc:
        logger.warning("Failed to fetch model list from %s: %s", settings.llm_chat_url, exc)

    tenant_thinking_mode = False
    tenant_id = x_tenant_id or "default"
    # For the model list we don't have a user context; use empty string to get default prefs.
    try:
        model_resolver: TenantModelResolver = http_request.app.state.model_resolver
        resolved = await model_resolver.resolve(tenant_id, "")
        tenant_thinking_mode = resolved.thinking_mode
    except Exception as exc:
        logger.warning("Failed to resolve tenant thinking_mode for %s: %s", tenant_id, exc)

    return ModelListResponse(
        models=models,
        default_model=settings.llm_model,
        tenant_thinking_mode=tenant_thinking_mode,
    )


# =============================================================================
# Settings cache invalidation
# =============================================================================

@app.delete("/internal/settings-cache/{tenant_id}", status_code=204)
async def invalidate_settings_cache(tenant_id: str, http_request: Request):
    """
    Invalidate the in-process TenantModelResolver cache for all users in a tenant.
    Called by the UI after tenant model settings change.
    """
    model_resolver: TenantModelResolver = http_request.app.state.model_resolver
    model_resolver.invalidate(tenant_id=tenant_id)
    logger.info("Model settings cache invalidated for tenant %s (all users)", tenant_id)


@app.delete("/internal/user-settings-cache", status_code=204)
async def invalidate_user_settings_cache(
    http_request: Request,
    x_tenant_id: str = Header(alias="X-Tenant-Id", default=""),
    x_user_id: str = Header(alias="X-User-Id", default=""),
):
    """
    Invalidate the in-process TenantModelResolver cache for a specific user.
    Called by the UI immediately after user preferences are saved.
    """
    if not x_tenant_id or not x_user_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id and X-User-Id headers required")
    model_resolver: TenantModelResolver = http_request.app.state.model_resolver
    model_resolver.invalidate(tenant_id=x_tenant_id, user_id=x_user_id)
    logger.info("User preferences cache invalidated for user=%s tenant=%s", x_user_id, x_tenant_id)


# =============================================================================
# Vector stats
# =============================================================================

@app.get("/vector-stats", response_model=VectorStatsResponse)
async def get_vector_stats(settings: SettingsDep, user_ctx: UserContextDep):
    from qdrant_client import QdrantClient
    from qdrant_client.http import models

    tenant_id = user_ctx.tenant_id
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
    user_ctx: UserContextDep,
    settings: SettingsDep,
    http_request: Request,
):
    """
    RAG query with tenant isolation and RBAC.
    Gateway claims always take precedence — clients cannot override tenant_id.
    """
    tenant_id = user_ctx.tenant_id
    user_roles = user_ctx.roles
    user_id = user_ctx.user_id

    try:
        result = await rag_service.query(
            question=request.question,
            tenant_id=tenant_id,
            user_roles=user_roles or None,
            user_id=user_id,
            document_type=request.document_type,
            top_k=request.top_k,
            conversation_id=request.conversation_id,
            min_score=request.min_score,
            user_context=user_ctx,
            summarizer=http_request.app.state.summarizer,
        )
        _fire_and_forget(_emit_query_event(
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
    user_ctx: UserContextDep,
    settings: SettingsDep,
    http_request: Request,
):
    """
    RAG query with streaming SSE response.

    Runs embedding + retrieval via the RAGService components,
    then streams the LLM response token-by-token.
    """
    import asyncio

    from ..components.llm_adapter import build_streaming_generator, extract_reasoning_content
    from ..pipelines.query import _build_section_label

    tenant_id = user_ctx.tenant_id
    user_roles = user_ctx.roles
    user_id = user_ctx.user_id

    llm_semaphore: asyncio.Semaphore = http_request.app.state.llm_semaphore
    model_resolver: TenantModelResolver = http_request.app.state.model_resolver
    summarizer = http_request.app.state.summarizer

    # Resolve effective model + thinking_mode before entering the generator.
    # thinking_mode is user-scoped; request.thinking_mode (per-query override) wins.
    # thinking is additionally gated on model capability.
    resolved = await model_resolver.resolve(tenant_id, user_id or "")
    effective_model = resolved.model
    effective_thinking = (
        request.thinking_mode if request.thinking_mode is not None else resolved.thinking_mode
    ) and _model_supports_thinking(effective_model)
    logger.warning(
        "Stream query — tenant=%s model=%s thinking=%s (tenant_pref=%s, req_override=%s, model_supports=%s)",
        tenant_id, effective_model, effective_thinking,
        resolved.thinking_mode, request.thinking_mode, _model_supports_thinking(effective_model),
    )

    async def generate():
        try:
            # Ensure RAGService is warmed up (embedders available)
            if not rag_service._ready:
                rag_service.warm_up()

            query_id = str(uuid.uuid4())
            loop = asyncio.get_running_loop()

            # Load conversation history (anchored summary + recent verbatim turns)
            # before emitting the metadata event so context_state is included upfront.
            stream_history: list[dict] = []
            context_state: dict = {}
            if request.conversation_id:
                stream_history, context_state = rag_service._load_conversation_history(
                    request.conversation_id, tenant_id
                )

            metadata_payload: dict = {"query_id": query_id, "cache_hit": False}
            if context_state.get("has_summary"):
                metadata_payload["context_state"] = context_state
            yield f"data: {json.dumps({'metadata': metadata_payload})}\n\n"

            # Embed — run sync calls in executor so they don't block the event loop
            embed_result = await loop.run_in_executor(
                None, lambda: rag_service._dense_embedder.run(text=request.question)
            )
            query_embedding = embed_result["embedding"]
            sparse_result = await loop.run_in_executor(
                None, lambda: rag_service._sparse_embedder.run(text=request.question)
            )
            query_sparse_embedding = sparse_result.get("sparse_embedding")

            # ── Semantic cache check ─────────────────────────────────────────
            if request.use_cache and rag_service._cache_checker:
                cache_result = await loop.run_in_executor(
                    None,
                    lambda: rag_service._cache_checker.run(  # type: ignore[union-attr]
                        query_embedding=query_embedding,
                        tenant_id=tenant_id,
                        user_context=user_ctx,
                    ),
                )
                if cache_result["cache_hit"]:
                    yield f"data: {json.dumps({'metadata': {'query_id': query_id, 'cache_hit': True}})}\n\n"
                    yield f"data: {json.dumps({'token': cache_result['cached_response']})}\n\n"
                    yield f"data: {json.dumps({'sources': cache_result.get('cached_sources', []), 'done': True})}\n\n"
                    return

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
                    from docintel_common.domain import get_domain_classifier
                    clf = get_domain_classifier()
                    result = await loop.run_in_executor(
                        None, lambda: clf.classify(request.question)
                    )
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

            retrieval_result = await loop.run_in_executor(
                None,
                lambda: rag_service._pipeline.get_component("retriever").run(  # type: ignore[union-attr]
                    query_embedding=query_embedding,
                    query_sparse_embedding=query_sparse_embedding,
                    tenant_id=tenant_id,
                    user_roles=user_roles or None,
                    user_id=user_id,
                    domain_filter=domain_filter,
                ),
            )

            # ── Rerank (same as /query endpoint) ────────────────────────────
            # Streaming path MUST rerank to match /query quality.
            top_k = request.top_k or settings.rag_default_top_k
            if request.use_reranking:
                try:
                    rerank_result = await loop.run_in_executor(
                        None,
                        lambda: rag_service._pipeline.get_component("reranker").run(  # type: ignore[union-attr]
                            query=request.question,
                            documents=retrieval_result["documents"],
                        ),
                    )
                    documents = rerank_result["documents"][:top_k]
                except Exception as _re:
                    logger.warning("Reranker failed in streaming path (falling back to retrieval order): %s", _re)
                    documents = retrieval_result["documents"][:top_k]
            else:
                documents = retrieval_result["documents"][:top_k]

            logger.info(
                "Retrieved %d documents (domain_filter=%s)",
                len(documents), routed_domain or "none",
            )

            if not documents:
                from ..prompts import NO_DOCUMENTS_RESPONSE, NO_RELEVANT_DOCUMENTS_RESPONSE
                from qdrant_client import QdrantClient as _QC
                try:
                    _qc = _QC(url=settings.qdrant_url)
                    _count = _qc.count(collection_name=f"documents_{tenant_id}", exact=False).count
                    response_text = (
                        NO_RELEVANT_DOCUMENTS_RESPONSE.format(query=request.question)
                        if _count > 0
                        else NO_DOCUMENTS_RESPONSE
                    )
                except Exception:
                    response_text = NO_DOCUMENTS_RESPONSE
                if request.conversation_id:
                    try:
                        from ..db import add_message
                        add_message(request.conversation_id, "user", request.question, tenant_id=tenant_id)
                        add_message(request.conversation_id, "assistant", response_text, tenant_id=tenant_id, sources=[])
                    except Exception as _e:
                        logger.warning("Failed to persist no-docs conversation: %s", _e)
                yield f"data: {json.dumps({'token': response_text})}\n\n"
                yield f"data: {json.dumps({'sources': [], 'done': True})}\n\n"
                return

            # Prompt — inject anchored conversation history
            prompt_result = rag_service._pipeline.get_component("prompt_builder").run(
                documents=documents,
                query=request.question,
                history=stream_history or None,
            )
            messages = prompt_result["messages"]

            # Stream LLM via Haystack's OpenAIChatGenerator (engine-agnostic).
            # The streaming_callback runs inside the executor thread, so we MUST use
            # call_soon_threadsafe to safely enqueue items into the event loop.
            # put_nowait alone is not thread-safe — it won't wake a coroutine
            # waiting on queue.get() if called from outside the event loop thread.
            queue: asyncio.Queue = asyncio.Queue()

            full_thinking = ""
            full_answer = ""
            llm_error: list = []  # mutable container so nonlocal works inside nested async def

            def streaming_callback(chunk):
                reasoning = extract_reasoning_content(chunk)
                if reasoning:
                    loop.call_soon_threadsafe(queue.put_nowait, ("thinking", reasoning))
                elif chunk.content:
                    loop.call_soon_threadsafe(queue.put_nowait, ("answer", chunk.content))

            # num_ctx: always set — default 4096 is too tight for RAG prompts that include
            # retrieved chunks + conversation history on top of the question.
            # max_tokens: None (omitted) for thinking so the engine uses its own unlimited
            # budget — -1 is Ollama-specific and rejected as invalid by LMForge/OpenAI.
            num_ctx = settings.llm_thinking_ctx if effective_thinking else settings.llm_ctx
            max_tokens = None if effective_thinking else settings.llm_max_tokens

            llm = build_streaming_generator(
                model=effective_model,
                chat_url=settings.llm_chat_url,
                api_key=settings.llm_api_key,
                streaming_callback=streaming_callback,
                think=effective_thinking,
                num_ctx=num_ctx,
                max_tokens=max_tokens,
                temperature=settings.llm_temperature,
                frequency_penalty=settings.llm_frequency_penalty,
            )

            # Notify the client if it will have to wait for an LLM slot.
            if llm_semaphore.locked():
                yield f"data: {json.dumps({'queued': True, 'message': 'Processing your request — a moment please...'})}\n\n"

            async def run_llm():
                async with llm_semaphore:
                    try:
                        # run_in_executor so the sync Ollama HTTP stream doesn't block the event loop.
                        await loop.run_in_executor(None, lambda: llm.run(messages=messages))
                    except asyncio.CancelledError:
                        logger.info("LLM task cancelled (client disconnected)")
                    except Exception as e:
                        logger.error("Streaming LLM failed: %s", e)
                        llm_error.append(e)
                    finally:
                        queue.put_nowait(None)

            task = asyncio.create_task(run_llm())

            try:
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
            except GeneratorExit:
                # Client disconnected — cancel the LLM task to free engine slot
                task.cancel()
                logger.info("Client disconnected mid-stream, LLM task cancelled")
                return

            await task

            # If LLM crashed with no output at all, surface the error to the client
            # instead of yielding empty answer + sources (which shows a blank panel).
            if llm_error and not full_answer and not full_thinking:
                yield f"data: {json.dumps({'error': f'LLM generation failed: {llm_error[0]}'})}\n\n"
                return

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

            # ── Semantic cache write (fire-and-forget, errors are non-fatal) ──
            if request.use_cache and rag_service._cache_writer and answer:
                _fire_and_forget(asyncio.wrap_future(loop.run_in_executor(
                    None,
                    lambda: rag_service._cache_writer.run(  # type: ignore[union-attr]
                        query=request.question,
                        query_embedding=query_embedding,
                        response=answer,
                        sources=sources,
                        tenant_id=tenant_id,
                    ),
                )))

            if request.conversation_id:
                try:
                    from ..db import add_message
                    import asyncio as _asyncio
                    add_message(request.conversation_id, "user", request.question, tenant_id=tenant_id)
                    add_message(request.conversation_id, "assistant", answer, tenant_id=tenant_id, sources=sources)
                    # Async fire-and-forget: compress history if threshold reached
                    _asyncio.ensure_future(
                        rag_service._maybe_compress_history(
                            request.conversation_id, tenant_id, summarizer
                        )
                    )
                except Exception as e:
                    logger.warning("Failed to persist streaming conversation: %s", e)

            # Fire-and-forget query telemetry to analytics-service.
            _fire_and_forget(_emit_query_event(
                query_id=query_id,
                tenant_id=tenant_id,
                user_id=user_id or "",
                latency_ms=0,
                model_used=effective_model,
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
# Conversation endpoints
# =============================================================================

class CreateConversationRequest(BaseModel):
    tenant_id: str = Field(default="default")
    user_id: Optional[str] = None
    title: str = Field(default="New Conversation")


class UpdateConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)


@app.post("/conversations")
async def create_conversation(request: CreateConversationRequest, user_ctx: UserContextDep):
    from ..db import create_conversation as _create
    tenant_id = user_ctx.tenant_id
    return await _run_db(
        lambda: _create(tenant_id=tenant_id, user_id=user_ctx.user_id or request.user_id, title=request.title)
    )


@app.get("/conversations")
async def list_conversations(
    user_ctx: UserContextDep,
    limit: int = 50,
    offset: int = 0,
):
    from ..db import list_conversations as _list
    return await _run_db(
        lambda: _list(tenant_id=user_ctx.tenant_id, user_id=user_ctx.user_id, limit=limit, offset=offset)
    )


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user_ctx: UserContextDep):
    from ..db import get_conversation as _get
    conv = await _run_db(lambda: _get(conversation_id, user_ctx.tenant_id))
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    user_ctx: UserContextDep,
):
    from ..db import update_conversation_title
    conv = await _run_db(lambda: update_conversation_title(conversation_id, user_ctx.tenant_id, request.title))
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str, user_ctx: UserContextDep):
    from ..db import delete_conversation
    deleted = await _run_db(lambda: delete_conversation(conversation_id, user_ctx.tenant_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}


# =============================================================================
# Internal service-to-service endpoints — no JWT, HMAC token only
# =============================================================================

@app.delete("/internal/conversations/tenant")
async def delete_tenant_conversations(
    x_tenant_id: str = Header(alias="X-Tenant-Id", default=""),
    x_internal_service_token: str = Header(alias="X-Internal-Service-Token", default=""),
):
    """
    Delete all conversations and messages for a tenant.
    Called by admin-service during tenant deletion.
    Auth: X-Internal-Service-Token (shared HMAC secret).
    """
    import os
    secret = os.environ.get("INTERNAL_GATEWAY_SECRET", "")
    if secret and x_internal_service_token != secret:
        raise HTTPException(status_code=403, detail="Invalid internal service token")

    if not x_tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-Id header required")

    from ..db import SessionLocal
    from sqlalchemy import text

    def _delete_all():
        with SessionLocal() as db:
            # platform_admin role to bypass RLS for cross-tenant admin operation
            db.execute(text("SET LOCAL app.user_role = 'platform_admin'"))
            db.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": x_tenant_id})
            result = db.execute(
                text(
                    "DELETE FROM conversations WHERE tenant_id = :tid"
                ),
                {"tid": x_tenant_id},
            )
            db.commit()
            return result.rowcount

    loop = asyncio.get_running_loop()
    deleted = await loop.run_in_executor(None, _delete_all)
    logger.info("Deleted %d conversations for tenant %s (internal API)", deleted, x_tenant_id)
    return {"deleted": deleted, "tenant_id": x_tenant_id}
