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

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..components.model_resolver import TenantModelResolver
from ..config import Settings
from ..context import _tenant_ctx, _role_ctx
from ..pipelines import RAGService
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


def _fire_and_forget(coro) -> asyncio.Task:
    """Create a task and attach an error-logging callback so exceptions are never silently dropped."""
    task = asyncio.create_task(coro)

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

    # Model resolver — resolves effective LLM per tenant from PostgreSQL (60s TTL cache)
    app.state.model_resolver = TenantModelResolver(
        postgres_url=settings.postgres_url,
        default_model=settings.ollama_llm_model,
    )

    # LLM concurrency gate — configurable via LLM_CONCURRENCY_LIMIT env var.
    # Ollama (serial GPU): 2-4. OpenAI/Anthropic API: 20-50+. vLLM: gpu_count * 4.
    app.state.llm_semaphore = asyncio.Semaphore(settings.llm_concurrency_limit)
    logger.info("LLM concurrency limit: %d", settings.llm_concurrency_limit)

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
    """Set tenant ContextVars and request-correlation logging context for every request."""
    request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    tenant_id  = request.headers.get("X-Tenant-Id", "default")
    user_id    = request.headers.get("X-User-Id", "")

    token_t = _tenant_ctx.set(tenant_id)
    token_r = _role_ctx.set(request.headers.get("X-User-Role", "tenant_user"))

    # Inject correlation fields into the logging context via LogRecord factory
    old_factory = logging.getLogRecordFactory()

    def _record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.request_id = request_id
        record.tenant_id  = tenant_id
        record.user_id    = user_id
        return record

    logging.setLogRecordFactory(_record_factory)
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response
    finally:
        _tenant_ctx.reset(token_t)
        _role_ctx.reset(token_r)
        logging.setLogRecordFactory(old_factory)


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
    ollama: str
    version: str = "0.1.0"


class ModelInfo(BaseModel):
    name: str
    size: Optional[int] = None
    modified_at: Optional[str] = None


class ModelListResponse(BaseModel):
    models: list[ModelInfo]
    default_model: str


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
# Model list (for dynamic model selection UI)
# =============================================================================

@app.get("/models", response_model=ModelListResponse)
async def list_models(settings: SettingsDep):
    """
    List all Ollama models available on the host.
    Used by the UI to populate the model selection dropdown.
    """
    models: list[ModelInfo] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            if r.status_code == 200:
                for m in r.json().get("models", []):
                    models.append(ModelInfo(
                        name=m.get("name", ""),
                        size=m.get("size"),
                        modified_at=m.get("modified_at"),
                    ))
    except Exception as exc:
        logger.warning("Failed to fetch Ollama model list: %s", exc)

    return ModelListResponse(
        models=models,
        default_model=settings.ollama_llm_model,
    )


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
        result = await rag_service.query(
            question=request.question,
            tenant_id=tenant_id,
            user_roles=user_roles or None,
            user_id=user_id,
            document_type=request.document_type,
            top_k=request.top_k,
            conversation_id=request.conversation_id,
            min_score=request.min_score,
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
    claims: JWTClaimsDep,
    settings: SettingsDep,
    http_request: Request,
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

    llm_semaphore: asyncio.Semaphore = http_request.app.state.llm_semaphore
    model_resolver: TenantModelResolver = http_request.app.state.model_resolver

    # Resolve the effective LLM model for this tenant before entering the generator.
    # This keeps the async DB lookup outside the sync generator closure.
    effective_model = await model_resolver.resolve(tenant_id)

    async def generate():
        try:
            # Ensure RAGService is warmed up (embedders available)
            if not rag_service._ready:
                rag_service.warm_up()

            query_id = str(uuid.uuid4())
            yield f"data: {json.dumps({'metadata': {'query_id': query_id, 'cache_hit': False}})}\n\n"

            # Embed — run sync calls in executor so they don't block the event loop
            loop = asyncio.get_running_loop()
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

            # Prompt
            prompt_result = rag_service._pipeline.get_component("prompt_builder").run(
                documents=documents, query=request.question
            )
            messages = prompt_result["messages"]

            # Stream LLM via Haystack's OllamaChatGenerator.
            # ollama-haystack ≥6.1 maps message.thinking → chunk.reasoning.reasoning_text
            # and message.content → chunk.content, giving clean separation without
            # any custom parsing or direct Ollama API calls.
            queue: asyncio.Queue = asyncio.Queue(maxsize=200)

            full_thinking = ""
            full_answer = ""

            # OllamaChatGenerator calls the streaming_callback SYNCHRONOUSLY inside
            # its HTTP response loop. An async def would return a coroutine object
            # that gets immediately discarded — the body would never execute.
            def streaming_callback(chunk):
                reasoning = getattr(chunk, "reasoning", None)
                if reasoning and getattr(reasoning, "reasoning_text", None):
                    queue.put_nowait(("thinking", reasoning.reasoning_text))
                elif chunk.content:
                    queue.put_nowait(("answer", chunk.content))

            llm = OllamaChatGenerator(
                model=effective_model,
                url=settings.ollama_base_url,
                think=True,
                generation_kwargs={
                    "temperature": settings.ollama_llm_temperature,
                    "num_predict": settings.ollama_llm_max_tokens,
                },
                streaming_callback=streaming_callback,
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
                # Client disconnected — cancel the LLM task to free Ollama
                task.cancel()
                logger.info("Client disconnected mid-stream, LLM task cancelled")
                return

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
                    add_message(request.conversation_id, "user", request.question, tenant_id=tenant_id)
                    add_message(request.conversation_id, "assistant", answer, tenant_id=tenant_id, sources=sources)
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
async def create_conversation(request: CreateConversationRequest, claims: JWTClaimsDep):
    from ..db import create_conversation as _create
    tenant_id = claims["tenant_id"]
    return await _run_db(
        lambda: _create(tenant_id=tenant_id, user_id=claims["user_id"] or request.user_id, title=request.title)
    )


@app.get("/conversations")
async def list_conversations(
    claims: JWTClaimsDep,
    limit: int = 50,
    offset: int = 0,
):
    from ..db import list_conversations as _list
    return await _run_db(
        lambda: _list(tenant_id=claims["tenant_id"], user_id=claims["user_id"], limit=limit, offset=offset)
    )


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, claims: JWTClaimsDep):
    from ..db import get_conversation as _get
    conv = await _run_db(lambda: _get(conversation_id, claims["tenant_id"]))
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
    conv = await _run_db(lambda: update_conversation_title(conversation_id, claims["tenant_id"], request.title))
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(conversation_id: str, claims: JWTClaimsDep):
    from ..db import delete_conversation
    deleted = await _run_db(lambda: delete_conversation(conversation_id, claims["tenant_id"]))
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}
