"""
Query Pipeline
==============

Haystack Pipeline for core RAG retrieval + generation.

Architecture (separation of concerns):

  ┌── Service layer (RAGService) ──────────────────────────────────────────┐
  │  1. Dense + BM25 embedding  (shared with cache check, called once)     │
  │  2. Semantic cache check    → early return on hit                       │
  │  3. Domain routing          → domain_filter for retriever               │
  │  4. Conversation history    → passed as prompt context                  │
  │  5. pipeline.run(...)       → core Haystack Pipeline                    │
  │  6. Build sources           → ref_id mapping for citations              │
  │  7. Cache write             → store response for future cache hits       │
  │  8. Conversation persist    → PostgreSQL                                 │
  └─────────────────────────────────────────────────────────────────────────┘
         ↓ pipeline.run()
  ┌── Haystack Pipeline (serialisable, warm-up managed, OTel-traceable) ─────┐
  │  SecureRetriever → OpaChunkValidator → InfinityReranker → PromptBuilder → LLM │
  └─────────────────────────────────────────────────────────────────────────┘

All LLM inference runs through an OpenAI-compatible endpoint (LMForge, Ollama, vLLM, etc.).
Dense embeddings go through LLM_EMBED_URL (Infinity or engine embed endpoint).
BM25 sparse embeddings use fastembed locally (no server, lightweight).
Reranker (cross-encoder) runs in Infinity — GPU/CPU via ONNX/TensorRT, decoupled from app.

Why embedders live outside the pipeline:
  Cache check requires the dense embedding *before* deciding whether to run
  the (expensive) pipeline. Keeping a single embedder instance avoids loading
  the model twice and removes redundant inference.
"""

import json
import logging
import threading
import time
from typing import Optional

from haystack import AsyncPipeline
from haystack.components.embedders.openai_text_embedder import OpenAITextEmbedder
from haystack.components.routers import TransformersZeroShotTextRouter
from haystack.utils import Secret

from ..components.cache import SemanticCacheChecker, SemanticCacheWriter
from ..components.embedders import BM25SparseTextEmbedder
from ..components.opa import OpaChunkValidator
from ..components.prompt import PromptBuilder
from ..components.reranker import InfinityReranker
from ..components.retrieval import SecureRetriever
from docintel_common.domain import DOMAIN_LABELS
from docintel_common.security import Classification, UserContext

from ..components.routing import DomainFilterBuilder
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------

def build_query_pipeline(settings: Settings) -> AsyncPipeline:
    """
    Build the core RAG Haystack AsyncPipeline.

    AsyncPipeline (Haystack ≥ 2.11) runs independent components concurrently
    via asyncio, and wraps sync components in run_in_executor automatically.
    Use `await pipeline.run_async(data=...)` for non-blocking execution.

    Inputs required from caller:
      retriever:      query_embedding, query_sparse_embedding, tenant_id,
                      user_roles, user_id, domain_filter
      reranker:       query
      prompt_builder: query, history (optional)

    Outputs available after run_async():
      retriever.documents, reranker.documents, llm.replies
    """
    pipeline = AsyncPipeline()

    pipeline.add_component(
        "retriever",
        SecureRetriever(settings=settings),
    )
    pipeline.add_component(
        "opa_validator",
        OpaChunkValidator(opa_url=settings.opa_url),
    )
    pipeline.add_component(
        "reranker",
        InfinityReranker(
            url=settings.reranker_url,
            model=settings.reranker_model,
            top_k=settings.rag_reranker_top_k,
        ),
    )
    pipeline.add_component("prompt_builder", PromptBuilder())

    from haystack.components.generators.chat.openai import OpenAIChatGenerator

    # Keep generation_kwargs in sync with streaming path in main.py so both
    # /query and /query/stream produce equivalent outputs for the same prompt.
    _gen_kwargs: dict = {
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    }
    if settings.llm_frequency_penalty:
        _gen_kwargs["frequency_penalty"] = settings.llm_frequency_penalty

    pipeline.add_component(
        "llm",
        OpenAIChatGenerator(
            model=settings.llm_model,
            api_base_url=settings.llm_chat_url,
            api_key=Secret.from_token(settings.llm_api_key),
            generation_kwargs=_gen_kwargs,
        ),
    )

    # Wire component outputs → inputs
    # retriever → opa_validator → reranker → prompt_builder → llm
    pipeline.connect("retriever.documents", "opa_validator.documents")
    pipeline.connect("opa_validator.documents", "reranker.documents")
    pipeline.connect("reranker.documents", "prompt_builder.documents")
    pipeline.connect("prompt_builder.messages", "llm.messages")

    return pipeline


# ---------------------------------------------------------------------------
# Section label helper (for source citations)
# ---------------------------------------------------------------------------

def _build_section_label(meta: dict, chunk_index: int) -> str:
    if meta.get("page") is not None:
        return f"p. {meta['page']}"
    item_idx = meta.get("item_index")
    doc_idx = meta.get("doc_index")
    if item_idx is not None and doc_idx is not None:
        return f"sample {item_idx}, doc {doc_idx}"
    return f"chunk {chunk_index}"


def _parse_json_answer(raw: str) -> str:
    """Extract 'answer' from JSON-wrapped LLM response, fallback to raw text."""
    try:
        parsed = json.loads(raw.strip())
        if isinstance(parsed, dict) and "answer" in parsed:
            return parsed["answer"]
    except (json.JSONDecodeError, TypeError):
        pass
    return raw


def _extract_think(raw: str) -> tuple[str, str]:
    """
    Split <think>...</think> reasoning from the answer text.

    Returns (thinking, answer). The thinking block is stripped from the
    answer so the two can be rendered separately in the UI.

    Handles three cases:
      1. Complete block: <think>...</think>remainder → (thinking, remainder)
      2. Unclosed block: <think>...EOF (generation truncated) → (tail, "")
         Prevents thinking tokens from leaking into the displayed answer.
      3. No block at all → ("", raw)
    """
    import re
    m = re.search(r"<think>(.*?)</think>(.*)", raw, re.DOTALL)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m_open = re.search(r"<think>(.*)$", raw, re.DOTALL)
    if m_open:
        return m_open.group(1).strip(), ""
    return "", raw.strip()


# ---------------------------------------------------------------------------
# RAGService
# ---------------------------------------------------------------------------

class RAGService:
    """
    High-level service that orchestrates the full RAG query flow.

    Owns:
      - Dense + sparse embedders (called before pipeline for cache check)
      - Semantic cache checker / writer
      - Domain router (optional)
      - Core Haystack Pipeline (retriever → reranker → prompt → llm)
      - Conversation helpers (PostgreSQL)

    Instantiated once at startup; warm_up() is called in the FastAPI lifespan.
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._ready = False
        self._warm_up_lock = threading.Lock()

        # Components managed outside the pipeline
        self._dense_embedder: Optional[OpenAITextEmbedder] = None
        self._sparse_embedder: Optional[BM25SparseTextEmbedder] = None
        self._cache_checker: Optional[SemanticCacheChecker] = None
        self._cache_writer: Optional[SemanticCacheWriter] = None
        self._domain_router: Optional[TransformersZeroShotTextRouter] = None
        self._domain_filter_builder: Optional[DomainFilterBuilder] = None

        # Core Haystack AsyncPipeline
        self._pipeline: Optional[AsyncPipeline] = None

    # ── Initialisation ───────────────────────────────────────────────────────

    def warm_up(self) -> None:
        """
        Initialise and warm up all components.

        Called once at application startup (FastAPI lifespan). Subsequent calls
        are no-ops. Thread-safe: concurrent calls block until the first completes.
        """
        if self._ready:
            return
        with self._warm_up_lock:
            if self._ready:  # re-check after acquiring lock
                return

            cfg = self._settings

            self._dense_embedder = OpenAITextEmbedder(
                model=cfg.llm_embed_model,
                api_base_url=cfg.llm_embed_url,
                api_key=Secret.from_token(cfg.llm_api_key),
            )

            # BM25 sparse embedder
            self._sparse_embedder = BM25SparseTextEmbedder()
            self._sparse_embedder.warm_up()

            # Semantic cache
            if cfg.use_cache:
                self._cache_checker = SemanticCacheChecker(
                    qdrant_url=cfg.qdrant_url,
                    threshold=cfg.rag_cache_similarity_threshold,
                )
                self._cache_writer = SemanticCacheWriter(qdrant_url=cfg.qdrant_url)

            # Domain router (optional)
            if cfg.rag_use_domain_routing:
                try:
                    self._domain_router = TransformersZeroShotTextRouter(
                        labels=DOMAIN_LABELS,
                        model=cfg.rag_domain_router_model,
                    )
                    self._domain_router.warm_up()
                    self._domain_filter_builder = DomainFilterBuilder()
                except Exception as e:
                    logger.warning("Domain router unavailable, routing disabled: %s", e)
                    self._domain_router = None

            # Core Haystack Pipeline
            self._pipeline = build_query_pipeline(cfg)
            self._pipeline.warm_up()

            self._ready = True
            logger.info("RAGService ready (cache=%s, domain_routing=%s)", cfg.use_cache, cfg.rag_use_domain_routing)

    # ── Conversation helpers ─────────────────────────────────────────────────

    def _load_conversation_history(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> tuple[list[dict], dict]:
        """
        Returns (history_messages, context_state).

        history_messages: list sent to LLM —
            [optional system summary msg] + last VERBATIM_RECENT user/assistant turns

        context_state: metadata for the SSE stream —
            {has_summary, summarized_turns, verbatim_turns}
        """
        cfg = self._settings
        verbatim = cfg.conversation_verbatim_recent

        empty_state = {"has_summary": False, "summarized_turns": 0, "verbatim_turns": 0}

        try:
            from ..db import get_conversation_summary_state, get_recent_messages

            state = get_conversation_summary_state(conversation_id, tenant_id)
            session_summary = state["session_summary"]
            summary_upto_count = state["summary_upto_count"]
            total = state["total_message_count"]

            # Exclude the current question (not yet persisted) from verbatim count
            recent = get_recent_messages(conversation_id, tenant_id, limit=verbatim)

            history: list[dict] = []
            if session_summary:
                history.append({
                    "role": "system",
                    "content": f"Earlier in this conversation:\n{session_summary}",
                })

            history.extend(recent)

            verbatim_count = len(recent)
            summarized_turns = summary_upto_count // 2  # messages → turns (pairs)

            context_state = {
                "has_summary": bool(session_summary),
                "summarized_turns": summarized_turns,
                "verbatim_turns": verbatim_count // 2,
            }

            return history, context_state

        except Exception as e:
            logger.warning("Could not load conversation history: %s", e)
        return [], empty_state

    def _persist_conversation(
        self,
        conversation_id: str,
        question: str,
        answer: str,
        sources: list[dict],
        tenant_id: str = "",
        summarizer=None,
    ) -> None:
        import asyncio

        try:
            from ..db import add_message

            add_message(conversation_id, "user", question, tenant_id=tenant_id)
            add_message(conversation_id, "assistant", answer, sources=sources, tenant_id=tenant_id)
        except Exception as e:
            logger.warning("Failed to persist conversation messages: %s", e)
            return

        if summarizer is not None:
            try:
                loop = asyncio.get_running_loop()
                asyncio.ensure_future(
                    self._maybe_compress_history(conversation_id, tenant_id, summarizer),
                    loop=loop,
                )
            except Exception as e:
                logger.warning("Failed to schedule history compression: %s", e)

    async def _maybe_compress_history(
        self,
        conversation_id: str,
        tenant_id: str,
        summarizer,
    ) -> None:
        """
        Anchored iterative compression (fire-and-forget).

        Triggers when total user/assistant messages exceed the threshold.
        Compresses only the newly-evictable span (not already in summary,
        not in the verbatim recent window) into the existing summary anchor.
        Writes a context_summary message so the UI can show a persistent divider.
        """
        cfg = self._settings
        threshold = cfg.conversation_summary_threshold
        verbatim = cfg.conversation_verbatim_recent

        try:
            from ..db import (
                get_conversation_summary_state,
                get_messages_slice,
                update_conversation_summary,
                add_message,
            )

            state = get_conversation_summary_state(conversation_id, tenant_id)
            total = state["total_message_count"]

            if total <= threshold:
                return

            summary_upto = state["summary_upto_count"]
            evictable_end = total - verbatim

            if evictable_end <= summary_upto:
                return  # nothing new to evict yet

            evictable_count = evictable_end - summary_upto
            if evictable_count < 2:
                return  # wait for at least one full turn

            new_span = get_messages_slice(
                conversation_id, tenant_id,
                offset=summary_upto,
                limit=evictable_count,
            )

            if not new_span:
                return

            new_summary = await summarizer.compress(
                existing_summary=state["session_summary"],
                new_span=new_span,
            )

            new_upto = summary_upto + len(new_span)
            update_conversation_summary(conversation_id, tenant_id, new_summary, new_upto)

            compressed_turns = len(new_span) // 2
            add_message(
                conversation_id,
                "context_summary",
                new_summary,
                tenant_id=tenant_id,
                metadata={
                    "type": "context_compression",
                    "compressed_turns": compressed_turns,
                    "summary_upto_count": new_upto,
                },
            )

            logger.info(
                "Conversation %s compressed: %d new turns absorbed (total summarized: %d)",
                conversation_id, compressed_turns, new_upto // 2,
            )

        except Exception as e:
            logger.warning("History compression failed (non-fatal): %s", e)

    # ── Domain routing helper ────────────────────────────────────────────────

    def _resolve_domain_filter(
        self,
        question: str,
        document_type: str | None,
    ) -> tuple[str | None, dict | None]:
        """
        Returns (detected_domain, domain_filter).

        Priority:
          1. Explicit document_type from caller
          2. Auto-detect via domain router (if enabled)
          3. None → search all domains
        """
        if document_type and document_type != "all":
            return document_type, {"key": "document_type", "match": {"value": document_type}}

        if self._domain_router and self._domain_filter_builder:
            try:
                router_output = self._domain_router.run(text=question)
                filter_result = self._domain_filter_builder.run(**router_output)
                return filter_result["detected_domain"], filter_result["domain_filter"]
            except Exception as e:
                logger.warning("Domain routing failed, searching all domains: %s", e)

        return None, None

    # ── Main query entry point ───────────────────────────────────────────────

    async def query(
        self,
        question: str,
        tenant_id: str,
        user_roles: list[str] | None = None,
        user_id: str | None = None,
        document_type: str | None = None,
        top_k: int | None = None,
        conversation_id: str | None = None,
        min_score: float | None = None,
        user_context: Optional[UserContext] = None,
        summarizer=None,
    ) -> dict:
        """
        Execute the full RAG query.

        Steps:
          1. Dense + sparse embedding
          2. Semantic cache check (early return on hit)
          3. Domain routing
          4. Load conversation history
          5. pipeline.run() — core Haystack Pipeline
          6. Apply top_k + min_score filters
          7. Build sources
          8. Cache write
          9. Conversation persist
        """
        import asyncio
        loop = asyncio.get_running_loop()

        if not self._ready:
            # warm_up() loads models and builds pipelines — must not block the event loop.
            await loop.run_in_executor(None, self.warm_up)

        cfg = self._settings
        effective_top_k = top_k if top_k is not None else cfg.rag_default_top_k
        effective_min_score = min_score if min_score is not None else cfg.rag_min_relevance_score
        start_time = time.time()

        # ── Step 1: Embed query ──────────────────────────────────────────────
        # OpenAITextEmbedder.run() makes a synchronous HTTP request to the embed endpoint.
        # BM25SparseTextEmbedder.run() is CPU-bound. Both are offloaded to a thread pool
        # to avoid blocking the uvicorn event loop.
        embed_result = await loop.run_in_executor(
            None, lambda: self._dense_embedder.run(text=question)  # type: ignore[union-attr]
        )
        query_embedding: list[float] = embed_result["embedding"]

        sparse_result = await loop.run_in_executor(
            None, lambda: self._sparse_embedder.run(text=question)  # type: ignore[union-attr]
        )
        query_sparse_embedding = sparse_result.get("sparse_embedding")

        # Build UserContext for OPA validation (use provided or construct from separate params)
        effective_user_ctx = user_context or UserContext(
            user_id=user_id or "",
            org_id=tenant_id,
            tenant_id=tenant_id,
            roles=user_roles or [],
            clearance=Classification.INTERNAL,
        )

        # ── Step 2: Cache check ──────────────────────────────────────────────
        if cfg.use_cache and self._cache_checker:
            cache_result = await loop.run_in_executor(
                None, lambda: self._cache_checker.run(  # type: ignore[union-attr]
                    query_embedding=query_embedding,
                    tenant_id=tenant_id,
                    user_context=effective_user_ctx,
                )
            )
            if cache_result["cache_hit"]:
                latency_ms = int((time.time() - start_time) * 1000)
                raw_sources = cache_result["cached_sources"] or []
                sources = [
                    {**s, "ref_id": s.get("ref_id", i + 1)} if isinstance(s, dict) else {"ref_id": i + 1}
                    for i, s in enumerate(raw_sources)
                ]
                return {
                    "answer": cache_result["cached_response"],
                    "sources": sources,
                    "cache_hit": True,
                    "latency_ms": latency_ms,
                    "model_used": "cache",
                }

        # ── Step 3: Domain routing ───────────────────────────────────────────
        detected_domain, domain_filter = self._resolve_domain_filter(question, document_type)

        # ── Step 4: Conversation history ─────────────────────────────────────
        history: list[dict] = []
        if conversation_id:
            history, _ = self._load_conversation_history(conversation_id, tenant_id)

        # ── Step 5: Core Haystack Pipeline ───────────────────────────────────
        pipeline_inputs = {
            "retriever": {
                "query_embedding": query_embedding,
                "query_sparse_embedding": query_sparse_embedding,
                "tenant_id": tenant_id,
                "user_roles": effective_user_ctx.roles,
                "user_id": effective_user_ctx.user_id,
                "domain_filter": domain_filter,
            },
            "opa_validator": {
                "user_context": effective_user_ctx,
            },
            "reranker": {"query": question},
            "prompt_builder": {
                "query": question,
                "history": history or None,
            },
        }

        result = await self._pipeline.run_async(data=pipeline_inputs)  # type: ignore[union-attr]

        # ── Step 6: Extract + filter results ─────────────────────────────────
        reranked_docs = result.get("reranker", {}).get("documents", [])

        if effective_min_score > 0.0:
            above = [d for d in reranked_docs if (d.score or 0.0) >= effective_min_score]
            if not above and cfg.rag_min_score_fallback_topk > 0:
                above = reranked_docs[:cfg.rag_min_score_fallback_topk]
            reranked_docs = above

        documents = reranked_docs[:effective_top_k]

        # No documents after filtering → return the standard no-docs response
        # rather than feeding an empty context to the LLM (degenerate answer).
        if not documents:
            from ..prompts import NO_DOCUMENTS_RESPONSE, NO_RELEVANT_DOCUMENTS_RESPONSE
            try:
                from qdrant_client import QdrantClient as _QC
                _qc = _QC(url=cfg.qdrant_url)
                _count = _qc.count(collection_name=f"documents_{tenant_id}", exact=False).count
                no_docs_text = (
                    NO_RELEVANT_DOCUMENTS_RESPONSE.format(query=question)
                    if _count > 0
                    else NO_DOCUMENTS_RESPONSE
                )
            except Exception:
                no_docs_text = NO_DOCUMENTS_RESPONSE
            latency_ms = int((time.time() - start_time) * 1000)
            if conversation_id:
                self._persist_conversation(conversation_id, question, no_docs_text, [], tenant_id=tenant_id, summarizer=summarizer)
            return {
                "answer": no_docs_text,
                "thinking": "",
                "sources": [],
                "cache_hit": False,
                "latency_ms": latency_ms,
                "model_used": cfg.llm_model,
                "detected_domain": detected_domain,
            }

        llm_replies = result.get("llm", {}).get("replies", [])
        raw_text = ""
        model_used = cfg.llm_model
        if llm_replies:
            reply = llm_replies[0]
            raw_text = getattr(reply, "text", None) or (
                reply.content[0].text if getattr(reply, "content", None) else ""
            )
            meta = getattr(reply, "meta", {}) or {}
            model_used = meta.get("model", cfg.llm_model) if isinstance(meta, dict) else cfg.llm_model

        thinking, answer = _extract_think(_parse_json_answer(raw_text))
        latency_ms = int((time.time() - start_time) * 1000)

        # ── Step 7: Build sources ─────────────────────────────────────────────
        sources = []
        for i, doc in enumerate(documents):
            chunk_idx = doc.meta.get("chunk_index", i)
            sources.append({
                "ref_id": i + 1,
                "chunk_id": doc.id,
                "document_id": doc.meta.get("document_id", ""),
                "filename": doc.meta.get("filename", "Unknown"),
                "section": _build_section_label(doc.meta, chunk_idx),
                "content": (_c := doc.content or "")[:200] + "..." if len(doc.content or "") > 200 else _c,
                "score": doc.score or 0.0,
                "metadata": {k: v for k, v in doc.meta.items() if k != "content"},
            })

        # ── Step 8: Cache write (non-blocking fire-and-forget) ───────────────
        if cfg.use_cache and self._cache_writer:
            import asyncio
            async def _write_cache():
                try:
                    await loop.run_in_executor(
                        None,
                        lambda: self._cache_writer.run(  # type: ignore[union-attr]
                            query=question,
                            query_embedding=query_embedding,
                            response=answer,
                            sources=sources,
                            tenant_id=tenant_id,
                        ),
                    )
                except Exception as e:
                    logger.warning("Cache write failed: %s", e)
            asyncio.create_task(_write_cache())

        # ── Step 9: Persist conversation ──────────────────────────────────────
        if conversation_id:
            self._persist_conversation(
                conversation_id, question, answer, sources,
                tenant_id=tenant_id, summarizer=summarizer,
            )

        return {
            "answer": answer,
            "thinking": thinking,
            "sources": sources,
            "cache_hit": False,
            "latency_ms": latency_ms,
            "model_used": model_used,
            "detected_domain": detected_domain,
        }
