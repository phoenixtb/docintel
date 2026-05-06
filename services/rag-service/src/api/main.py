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

from docintel_common.tracing import TraceContext, configure_trace_logging

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..components.model_profile_resolver import ModelProfileResolver
from ..components.model_resolver import TenantModelResolver
from ..config import Settings
from ..context import _tenant_ctx, _role_ctx
from ..events import (
    ErrorEvent,
    MetadataEvent,
    PipelineEvent,
    QueuedEvent,
    RoutingEvent,
    SourcesEvent,
    StatusEvent,
    ThinkingTokenEvent,
    TokenEvent,
)
from ..pipelines import RAGService
from ..tracing import LangfuseTracer
from ..utils.asyncio import _run_db
from .dependencies import ConversationHistoryDep, RAGServiceDep, SettingsDep, TracerDep, UserContextDep
from .schemas import QueryRequest, QueryResponse

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
    thinking_truncated: bool = False,
    http_client: Optional[httpx.AsyncClient] = None,
) -> None:
    """Fire-and-forget: POST query telemetry to analytics-service."""
    try:
        client = http_client or httpx.AsyncClient(timeout=3.0)
        close_after = http_client is None
        try:
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
                    "thinking_truncated": thinking_truncated,
                },
                timeout=3.0,
            )
        finally:
            if close_after:
                await client.aclose()
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

    # Fail-fast guard: verify Haystack private symbols used by ThinkingAwareChatGenerator
    # are still present. Catches silent breakage from a Haystack upgrade before any query.
    _selftest_thinking_adapter()

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

    # Model profile resolver — resolves sampling params (temperature, top_p, etc.)
    # per (model_name, tenant_id). Chain: tenant DB → platform DB → built-in → env config.
    app.state.model_profile_resolver = ModelProfileResolver(
        postgres_url=settings.postgres_url,
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

    # Shared HTTP client — connection pool reused across requests (healthcheck,
    # model list, analytics telemetry). Avoids opening a new TCP connection per
    # call. The probe / pre-warm helpers use their own short-lived clients since
    # they fire only at startup before the pool is needed.
    app.state.http = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
    )

    yield

    logger.info("RAG Service shutting down")
    tracer.shutdown()
    await app.state.http.aclose()


def _selftest_thinking_adapter() -> None:
    """
    Verify that Haystack's private symbols used by ThinkingAwareChatGenerator
    still exist. Fails loudly at startup so broken Haystack upgrades are caught
    before any user query rather than mid-stream.
    """
    from ..components.llm_adapter import ThinkingAwareChatGenerator
    from haystack.components.generators.chat.openai import OpenAIChatGenerator

    if not hasattr(OpenAIChatGenerator, "_handle_stream_response"):
        raise RuntimeError(
            "Haystack OpenAIChatGenerator._handle_stream_response is missing. "
            "ThinkingAwareChatGenerator's override will not fire — thinking tokens "
            "will be silently dropped. Update llm_adapter.py or pin haystack-ai~=2.18."
        )
    logger.info("ThinkingAwareChatGenerator self-test passed")


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
        loop = asyncio.get_running_loop()
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

# QueryRequest and QueryResponse live in schemas.py (imported above) to avoid
# circular imports with dependencies.py. HealthResponse stays here — it's not
# needed by dependencies.


class HealthResponse(BaseModel):
    status: str
    qdrant: str
    llm_engine: str
    version: str = "0.1.0"


# Model families known to support the Ollama thinking/reasoning API.
# Checked via substring match against the lowercased model name.
_THINKING_MODEL_FAMILIES = {"qwen3", "qwq", "deepseek-r1", "marco-o1", "skywork-o1", ":r1"}

# Substring rules for capability inference when LMForge does not return a
# `capabilities` block. Keep these in lockstep with the UI's `inferKind`.
_VISION_KEYWORDS  = ("-vl", ":vl", "vision", "llava", "moondream", "internvl")
_RERANK_KEYWORDS  = ("rerank", "reranker")
_EMBED_KEYWORDS   = ("embed", "nomic", "mxbai", "bge", "gte", "e5-")


def _model_supports_thinking(model_name: str) -> bool:
    name_lower = model_name.lower()
    return any(family in name_lower for family in _THINKING_MODEL_FAMILIES)


def _model_supports_vision(model_name: str) -> bool:
    n = model_name.lower()
    return any(kw in n for kw in _VISION_KEYWORDS) or n.startswith("vl")


def _model_is_reranker(model_name: str) -> bool:
    n = model_name.lower()
    return any(kw in n for kw in _RERANK_KEYWORDS)


def _model_is_embed(model_name: str) -> bool:
    n = model_name.lower()
    return any(kw in n for kw in _EMBED_KEYWORDS)


class ModelInfo(BaseModel):
    name: str
    size: Optional[int] = None
    modified_at: Optional[str] = None
    supports_thinking: bool = False
    supports_vision: bool = False
    is_reranker: bool = False
    is_embed: bool = False


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
async def health_check(settings: SettingsDep, http_request: Request):
    qdrant_status = "unknown"
    llm_status = "unknown"

    try:
        from qdrant_client import QdrantClient
        QdrantClient(url=settings.qdrant_url).get_collections()
        qdrant_status = "connected"
    except Exception as e:
        qdrant_status = f"error: {str(e)[:50]}"

    try:
        client: httpx.AsyncClient = http_request.app.state.http
        r = await client.get(f"{settings.llm_chat_url}/models", timeout=5.0)
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
    List all models available from the configured LLM engine, with capability
    flags so the UI can filter per-kind dropdowns (chat / vlm / rerank).

    Embed models are returned for completeness but the UI typically hides them
    because the embed model is env-locked.
    """
    models: list[ModelInfo] = []
    try:
        client: httpx.AsyncClient = http_request.app.state.http
        r = await client.get(f"{settings.llm_chat_url}/models", timeout=10.0)
        if r.status_code == 200:
            raw_models = r.json().get("data", [])
            for m in raw_models:
                name: str = m.get("id", "") or m.get("name", "")
                if not name:
                    continue
                # LMForge optionally returns a `capabilities` block — use it
                # when present, fall back to substring inference otherwise.
                capabilities: dict = m.get("capabilities", {})
                supports_thinking = capabilities.get("thinking", _model_supports_thinking(name))
                supports_vision   = capabilities.get("vision",   _model_supports_vision(name))
                is_reranker       = capabilities.get("rerank",   _model_is_reranker(name))
                is_embed          = capabilities.get("embed",    _model_is_embed(name))
                models.append(ModelInfo(
                    name=name,
                    size=m.get("size"),
                    modified_at=m.get("modified_at") or m.get("created"),
                    supports_thinking=supports_thinking,
                    supports_vision=supports_vision,
                    is_reranker=is_reranker,
                    is_embed=is_embed,
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
# Model profile cache invalidation
# =============================================================================

@app.get("/internal/model-profiles/resolve/{tenant_id}")
async def resolve_model_profile(tenant_id: str, model: str, http_request: Request):
    """
    Return fully-resolved effective sampling parameters for a (tenant, model) pair.
    Resolution chain: tenant DB → platform DB → built-in defaults → env var fallbacks.
    All values are guaranteed non-null (env fallbacks applied).
    """
    resolver: ModelProfileResolver = http_request.app.state.model_profile_resolver
    settings: Settings = http_request.app.state.settings
    params = await resolver.resolve(model, tenant_id)

    def _v(resolved, fallback):
        return resolved if resolved is not None else fallback

    return {
        "model": model,
        "tenant_id": tenant_id,
        # Standard mode
        "temperature": _v(params.temperature, settings.llm_temperature),
        "top_p": _v(params.top_p, settings.llm_top_p),
        "max_tokens": _v(params.max_tokens, settings.llm_max_tokens),
        "frequency_penalty": _v(params.frequency_penalty, settings.llm_frequency_penalty),
        "presence_penalty": _v(params.presence_penalty, settings.llm_presence_penalty),
        "repetition_penalty": _v(params.repetition_penalty, settings.llm_repetition_penalty),
        "top_k": _v(params.top_k, settings.llm_top_k),
        "min_p": _v(params.min_p, settings.llm_min_p),
        # Thinking mode
        "thinking_temperature": _v(params.thinking_temperature, settings.llm_thinking_temperature),
        "thinking_top_p": _v(params.thinking_top_p, settings.llm_thinking_top_p),
        "thinking_max_tokens": _v(params.thinking_max_tokens, settings.llm_thinking_max_tokens),
        "thinking_frequency_penalty": _v(params.thinking_frequency_penalty, settings.llm_thinking_frequency_penalty),
        "thinking_presence_penalty": _v(params.thinking_presence_penalty, settings.llm_thinking_presence_penalty),
        "thinking_repetition_penalty": _v(params.thinking_repetition_penalty, settings.llm_thinking_repetition_penalty),
        "thinking_top_k": _v(params.thinking_top_k, settings.llm_thinking_top_k),
        "thinking_min_p": _v(params.thinking_min_p, settings.llm_thinking_min_p),
        "thinking_budget": _v(params.thinking_budget, settings.llm_thinking_budget),
    }


@app.delete("/internal/model-profiles-cache", status_code=204)
async def invalidate_model_profiles_cache_global(http_request: Request):
    """
    Flush the entire ModelProfileResolver cache (platform profiles changed).
    Called by the UI after platform-scope model profile CRUD.
    """
    resolver: ModelProfileResolver = http_request.app.state.model_profile_resolver
    resolver.invalidate()
    logger.info("Model profile cache fully invalidated (platform profiles changed)")


@app.delete("/internal/model-profiles-cache/{tenant_id}", status_code=204)
async def invalidate_model_profiles_cache_tenant(tenant_id: str, http_request: Request):
    """
    Flush ModelProfileResolver cache entries for a specific tenant.
    Called by the UI after tenant-scope model profile CRUD.
    """
    resolver: ModelProfileResolver = http_request.app.state.model_profile_resolver
    resolver.invalidate(tenant_id=tenant_id)
    logger.info("Model profile cache invalidated for tenant %s", tenant_id)


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
# SSE serialiser
# =============================================================================

def _serialize_sse(event: PipelineEvent) -> str:
    """Convert a typed PipelineEvent into an SSE data line, preserving wire format."""
    match event:
        case MetadataEvent(query_id=qid, cache_hit=ch, context_state=cs):
            payload: dict = {"metadata": {"query_id": qid, "cache_hit": ch}}
            if cs:
                payload["metadata"]["context_state"] = cs
        case RoutingEvent(domain=d, explicit=e):
            payload = {"routing": {"domain": d, "explicit": e}}
        case QueuedEvent(message=m):
            payload = {"queued": True, "message": m}
        case ThinkingTokenEvent(text=t):
            payload = {"thinking_token": t}
        case StatusEvent(stage=s):
            payload = {"status": s}
        case TokenEvent(text=t):
            payload = {"token": t}
        case SourcesEvent(sources=s, done=d):
            payload = {"sources": list(s), "done": d}
        case ErrorEvent(message=m):
            payload = {"error": m}
        case _:
            payload = {}
    return f"data: {json.dumps(payload)}\n\n"


# =============================================================================
# Query endpoints
# =============================================================================

def _resolve_effective_model_and_thinking(
    request: QueryRequest,
    resolved,
    effective_model_name: str,
) -> tuple[str, bool]:
    """Compute effective_model and effective_thinking from resolver + request override."""
    effective_thinking = (
        request.thinking_mode if request.thinking_mode is not None else resolved.thinking_mode
    ) and _model_supports_thinking(effective_model_name)
    return effective_model_name, effective_thinking


@app.post("/query", response_model=QueryResponse)
async def query_documents(
    request: QueryRequest,
    rag_service: RAGServiceDep,
    user_ctx: UserContextDep,
    settings: SettingsDep,
    history: ConversationHistoryDep,
    http_request: Request,
):
    """
    RAG query with tenant isolation and RBAC.
    Gateway claims always take precedence — clients cannot override tenant_id.
    """
    model_resolver: TenantModelResolver = http_request.app.state.model_resolver
    llm_semaphore: asyncio.Semaphore = http_request.app.state.llm_semaphore
    resolved = await model_resolver.resolve(user_ctx.tenant_id, user_ctx.user_id or "")
    effective_model, effective_thinking = _resolve_effective_model_and_thinking(
        request, resolved, resolved.model
    )
    request_id = str(uuid.uuid4())

    try:
        result = await rag_service.query(
            question=request.question,
            tenant_id=user_ctx.tenant_id,
            user_context=user_ctx,
            user_roles=user_ctx.roles or None,
            user_id=user_ctx.user_id,
            history=history.messages,
            context_state=history.context_state,
            document_type=request.document_type,
            top_k=request.top_k,
            min_score=request.min_score,
            use_cache=request.use_cache,
            use_reranking=request.use_reranking,
            effective_model=effective_model,
            effective_thinking=effective_thinking,
            settings=settings,
            llm_semaphore=llm_semaphore,
            request_id=request_id,
            model_profile_resolver=http_request.app.state.model_profile_resolver,
            conversation_id=request.conversation_id,
            summarizer=http_request.app.state.summarizer,
        )
        _fire_and_forget(_emit_query_event(
            query_id=request_id,
            tenant_id=user_ctx.tenant_id,
            user_id=user_ctx.user_id or "",
            latency_ms=result["latency_ms"],
            model_used=result.get("model_used", "unknown"),
            cache_hit=result["cache_hit"],
            source_count=len(result["sources"]),
            analytics_url=settings.analytics_service_url,
            thinking_truncated=result.get("thinking_truncated", False),
            http_client=http_request.app.state.http,
        ))
        return QueryResponse(
            answer=result["answer"],
            thinking=result.get("thinking", ""),
            sources=result["sources"],
            cache_hit=result["cache_hit"],
            latency_ms=result["latency_ms"],
            model_used=result.get("model_used", "unknown"),
            routed_domain=result.get("detected_domain"),
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
    history: ConversationHistoryDep,
    http_request: Request,
):
    """Thin streaming handler — delegates all orchestration to RAGService.stream()."""
    llm_semaphore: asyncio.Semaphore = http_request.app.state.llm_semaphore
    model_resolver: TenantModelResolver = http_request.app.state.model_resolver
    resolved = await model_resolver.resolve(user_ctx.tenant_id, user_ctx.user_id or "")
    effective_model, effective_thinking = _resolve_effective_model_and_thinking(
        request, resolved, resolved.model
    )
    logger.info(
        "Stream query — tenant=%s model=%s thinking=%s",
        user_ctx.tenant_id, effective_model, effective_thinking,
    )
    request_id = str(uuid.uuid4())

    async def sse_iter():
        cache_hit = False
        source_count = 0
        try:
            async for event in rag_service.stream(
                question=request.question,
                tenant_id=user_ctx.tenant_id,
                user_context=user_ctx,
                user_roles=user_ctx.roles or None,
                user_id=user_ctx.user_id,
                history=history.messages,
                context_state=history.context_state,
                document_type=request.document_type,
                top_k=request.top_k,
                min_score=request.min_score,
                use_cache=request.use_cache,
                use_reranking=request.use_reranking,
                effective_model=effective_model,
                effective_thinking=effective_thinking,
                settings=settings,
                llm_semaphore=llm_semaphore,
                request_id=request_id,
                model_profile_resolver=http_request.app.state.model_profile_resolver,
                conversation_id=request.conversation_id,
                summarizer=http_request.app.state.summarizer,
            ):
                if isinstance(event, MetadataEvent):
                    cache_hit = event.cache_hit
                elif isinstance(event, SourcesEvent):
                    source_count = len(event.sources)
                yield _serialize_sse(event)
        except Exception as e:
            logger.exception("Streaming query failed")
            yield _serialize_sse(ErrorEvent(message=str(e)))
            return

        _fire_and_forget(_emit_query_event(
            query_id=request_id,
            tenant_id=user_ctx.tenant_id,
            user_id=user_ctx.user_id or "",
            latency_ms=0,
            model_used=effective_model,
            cache_hit=cache_hit,
            source_count=source_count,
            analytics_url=settings.analytics_service_url,
            thinking_truncated=getattr(rag_service, "_last_thinking_truncated", False),
            http_client=http_request.app.state.http,
        ))

    return StreamingResponse(
        sse_iter(),
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
