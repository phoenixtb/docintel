"""
RAGService — unified query orchestration.

Both /query and /query/stream go through RAGService.stream(), an async generator
that yields typed PipelineEvents.  RAGService.query() drains that generator into
the legacy dict shape consumed by /query.

Flow (stream):
  1. MetadataEvent(cache_hit=False)
  2. Dense + sparse embed
  3. Cache check → on hit: typewriter TokenEvents → SourcesEvent → return
  4. Route (domain filter) → RoutingEvent
  5. SecureRetriever
  6. OpaChunkValidator (A6/A7 — closes streaming security gap)
  7. Rerank (optional)
  8. Min-score / top-k filter
  9. No-docs branch → TokenEvent(sentinel) → SourcesEvent(empty) → return
 10. PromptBuilder
 11. QueuedEvent (if semaphore saturated)
 12. Streaming LLM → ThinkingTokenEvent* / TokenEvent+
 13. Build sources
 14. Cache write (fire-and-forget)
 15. Persist conversation (fire-and-forget)
 16. SourcesEvent(done=True)
"""

import asyncio
import json
import logging
import threading
import time
from typing import AsyncIterator, Optional

from haystack.components.embedders.openai_text_embedder import OpenAITextEmbedder
from haystack.components.routers import TransformersZeroShotTextRouter
from haystack.utils import Secret

from ..components.cache import SemanticCacheChecker, SemanticCacheWriter
from ..components.embedders import BM25SparseTextEmbedder
from ..components.llm_adapter import build_streaming_generator, extract_lmforge_status, extract_reasoning_content
from ..components.model_profile_resolver import ModelProfileResolver
from ..components.opa import OpaChunkValidator
from ..components.prompt import PromptBuilder
from ..components.reranker import InfinityReranker
from ..components.retrieval import SecureRetriever
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
from ..utils.asyncio import _run_db
from docintel_common.domain import DOMAIN_LABELS
from docintel_common.security import Classification, UserContext

from ..components.routing import DomainFilterBuilder
from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


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


def _build_sources(documents: list) -> list[dict]:
    """Build the canonical source list from Haystack Document objects."""
    sources = []
    for i, doc in enumerate(documents):
        chunk_idx = doc.meta.get("chunk_index", i)
        content = doc.content or ""
        sources.append({
            "ref_id": i + 1,
            "chunk_id": doc.id,
            "document_id": doc.meta.get("document_id", ""),
            "filename": doc.meta.get("filename", "Unknown"),
            "section": _build_section_label(doc.meta, chunk_idx),
            "chunk_index": chunk_idx,
            "score": doc.score or 0.0,
            "content": content[:600],
            "domain": doc.meta.get("document_type") or doc.meta.get("domain") or "",
            "metadata": {k: v for k, v in doc.meta.items() if k != "content"},
        })
    return sources


# ---------------------------------------------------------------------------
# RAGService
# ---------------------------------------------------------------------------

class RAGService:
    """
    High-level service that orchestrates the full RAG query flow.

    Both /query and /query/stream use RAGService.stream(), which is the single
    source of truth for the embed → cache → route → retrieve → opa → rerank →
    prompt → llm flow.

    Owns:
      - Dense + sparse embedders
      - Semantic cache checker / writer
      - Domain router (optional)
      - Retriever, OPA validator, reranker, prompt builder (direct references,
        no longer wrapped in a Haystack pipeline at runtime)
      - Conversation helpers (PostgreSQL)

    Instantiated once at startup; warm_up() is called in the FastAPI lifespan.
    """

    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()
        self._ready = False
        self._warm_up_lock = threading.Lock()

        # Embedders
        self._dense_embedder: Optional[OpenAITextEmbedder] = None
        self._sparse_embedder: Optional[BM25SparseTextEmbedder] = None

        # Semantic cache
        self._cache_checker: Optional[SemanticCacheChecker] = None
        self._cache_writer: Optional[SemanticCacheWriter] = None

        # Domain routing
        self._domain_router: Optional[TransformersZeroShotTextRouter] = None
        self._domain_filter_builder: Optional[DomainFilterBuilder] = None

        # Core RAG components (direct references, replacing Haystack pipeline)
        self._retriever: Optional[SecureRetriever] = None
        self._opa_validator: Optional[OpaChunkValidator] = None
        self._reranker: Optional[InfinityReranker] = None
        self._prompt_builder: Optional[PromptBuilder] = None

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
            if self._ready:
                return

            cfg = self._settings

            self._dense_embedder = OpenAITextEmbedder(
                model=cfg.llm_embed_model,
                api_base_url=cfg.llm_embed_url,
                api_key=Secret.from_token(cfg.llm_api_key),
            )

            self._sparse_embedder = BM25SparseTextEmbedder()
            self._sparse_embedder.warm_up()

            if cfg.use_cache:
                self._cache_checker = SemanticCacheChecker(
                    qdrant_url=cfg.qdrant_url,
                    threshold=cfg.rag_cache_similarity_threshold,
                )
                self._cache_writer = SemanticCacheWriter(qdrant_url=cfg.qdrant_url)

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

            # Core components — instantiate and warm up individually (no pipeline wrapper)
            self._retriever = SecureRetriever(settings=cfg)
            self._opa_validator = OpaChunkValidator(opa_url=cfg.opa_url)
            self._reranker = InfinityReranker(
                url=cfg.reranker_url,
                model=cfg.reranker_model,
                top_k=cfg.rag_reranker_top_k,
            )
            self._prompt_builder = PromptBuilder()

            self._ready = True
            logger.info(
                "RAGService ready (cache=%s, domain_routing=%s)",
                cfg.use_cache, cfg.rag_use_domain_routing,
            )

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

            recent = get_recent_messages(conversation_id, tenant_id, limit=verbatim)

            history: list[dict] = []
            if session_summary:
                history.append({
                    "role": "system",
                    "content": f"Earlier in this conversation:\n{session_summary}",
                })

            history.extend(recent)

            verbatim_count = len(recent)
            summarized_turns = summary_upto_count // 2

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
        Compresses only the newly-evictable span into the existing summary anchor.
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
                return

            evictable_count = evictable_end - summary_upto
            if evictable_count < 2:
                return

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

    # ── Primary streaming generator ──────────────────────────────────────────

    async def stream(
        self,
        *,
        question: str,
        tenant_id: str,
        user_context: UserContext,
        user_roles: list[str] | None,
        user_id: str | None,
        history: list[dict] | None,
        context_state: dict | None,
        document_type: str | None,
        top_k: int | None,
        min_score: float | None,
        use_cache: bool,
        use_reranking: bool,
        effective_model: str,
        effective_thinking: bool,
        settings: Settings,
        llm_semaphore: asyncio.Semaphore,
        request_id: str,
        model_profile_resolver: ModelProfileResolver | None = None,
        conversation_id: str | None = None,
        summarizer=None,
    ) -> AsyncIterator[PipelineEvent]:
        """
        Unified RAG streaming generator — single source of truth for both paths.

        Yields typed PipelineEvents in stream order. Callers serialize to SSE
        (streaming handler) or aggregate into a dict (query handler).
        """
        loop = asyncio.get_running_loop()
        cfg = settings
        effective_top_k = top_k if top_k is not None else cfg.rag_default_top_k
        effective_min_score = min_score if min_score is not None else cfg.rag_min_relevance_score

        if not self._ready:
            await loop.run_in_executor(None, self.warm_up)

        # ── 1. Initial metadata ───────────────────────────────────────────────
        initial_context = context_state if (context_state and context_state.get("has_summary")) else None
        yield MetadataEvent(
            query_id=request_id,
            cache_hit=False,
            context_state=initial_context,
        )

        # ── 2. Embed ──────────────────────────────────────────────────────────
        try:
            embed_result = await loop.run_in_executor(
                None, lambda: self._dense_embedder.run(text=question)  # type: ignore[union-attr]
            )
            query_embedding: list[float] = embed_result["embedding"]

            sparse_result = await loop.run_in_executor(
                None, lambda: self._sparse_embedder.run(text=question)  # type: ignore[union-attr]
            )
            query_sparse_embedding = sparse_result.get("sparse_embedding")
        except Exception as e:
            logger.error("Embedding failed: %s", e)
            yield ErrorEvent(message=f"Embedding failed: {e}")
            return

        # ── 3. Cache check ────────────────────────────────────────────────────
        if use_cache and self._cache_checker:
            try:
                cache_result = await loop.run_in_executor(
                    None,
                    lambda: self._cache_checker.run(  # type: ignore[union-attr]
                        query_embedding=query_embedding,
                        tenant_id=tenant_id,
                        user_context=user_context,
                    ),
                )
                if cache_result["cache_hit"]:
                    yield MetadataEvent(query_id=request_id, cache_hit=True)
                    cached_text: str = cache_result["cached_response"] or ""
                    chunk_size = cfg.rag_cache_replay_chunk_chars
                    delay_ms = cfg.rag_cache_replay_chunk_delay_ms
                    for i in range(0, len(cached_text), chunk_size):
                        yield TokenEvent(text=cached_text[i: i + chunk_size])
                        if delay_ms > 0:
                            await asyncio.sleep(delay_ms / 1000)
                    yield SourcesEvent(
                        sources=list(cache_result.get("cached_sources") or []),
                        done=True,
                    )
                    return
            except Exception as e:
                logger.warning("Cache check failed (continuing without cache): %s", e)

        # ── 4. Route ──────────────────────────────────────────────────────────
        try:
            detected_domain, domain_filter = await loop.run_in_executor(
                None,
                lambda: self._resolve_domain_filter(question, document_type),
            )
        except Exception as e:
            logger.warning("Domain routing failed (fallback to all): %s", e)
            detected_domain, domain_filter = None, None

        explicit = bool(document_type and document_type != "all")
        yield RoutingEvent(domain=detected_domain, explicit=explicit)

        # ── 5. Retrieve ───────────────────────────────────────────────────────
        try:
            retrieval_result = await loop.run_in_executor(
                None,
                lambda: self._retriever.run(  # type: ignore[union-attr]
                    query_embedding=query_embedding,
                    query_sparse_embedding=query_sparse_embedding,
                    tenant_id=tenant_id,
                    user_roles=user_roles or None,
                    user_id=user_id,
                    domain_filter=domain_filter,
                ),
            )
            retrieved_docs = retrieval_result["documents"]
        except Exception as e:
            logger.error("Retrieval failed: %s", e)
            yield ErrorEvent(message=f"Retrieval failed: {e}")
            return

        # ── 6. OPA validate (A6/A7) ───────────────────────────────────────────
        if retrieved_docs:
            try:
                opa_result = await loop.run_in_executor(
                    None,
                    lambda: self._opa_validator.run(  # type: ignore[union-attr]
                        documents=retrieved_docs,
                        user_context=user_context,
                        request_id=request_id,
                    ),
                )
                documents = opa_result["documents"]
            except Exception as e:
                logger.error("OPA validation failed (fail-closed): %s", e)
                yield ErrorEvent(message=f"Access policy check failed: {e}")
                return
        else:
            documents = []

        # ── 7. Rerank ─────────────────────────────────────────────────────────
        if use_reranking and documents:
            try:
                rerank_result = await loop.run_in_executor(
                    None,
                    lambda: self._reranker.run(  # type: ignore[union-attr]
                        query=question,
                        documents=documents,
                    ),
                )
                documents = rerank_result["documents"]
            except Exception as e:
                logger.warning("Reranker failed, falling back to retrieval order: %s", e)

        # ── 8. Min-score / top-k filter ───────────────────────────────────────
        if effective_min_score > 0.0:
            above = [d for d in documents if (d.score or 0.0) >= effective_min_score]
            if not above and cfg.rag_min_score_fallback_topk > 0:
                above = documents[:cfg.rag_min_score_fallback_topk]
            documents = above

        documents = documents[:effective_top_k]

        # ── 9. No-docs branch ─────────────────────────────────────────────────
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

            if conversation_id:
                _cid_no_docs = conversation_id
                _q_no_docs = question
                _t_no_docs = tenant_id
                _txt_no_docs = no_docs_text

                async def _persist_no_docs():
                    from ..db import add_message as _add
                    try:
                        await _run_db(lambda: _add(_cid_no_docs, "user", _q_no_docs, tenant_id=_t_no_docs))
                        await _run_db(lambda: _add(_cid_no_docs, "assistant", _txt_no_docs, tenant_id=_t_no_docs, sources=[]))
                    except Exception as _e:
                        logger.warning("Failed to persist no-docs conversation: %s", _e)
                asyncio.create_task(_persist_no_docs())

            yield TokenEvent(text=no_docs_text)
            yield SourcesEvent(sources=[], done=True)
            return

        # ── 10. Prompt build ──────────────────────────────────────────────────
        try:
            prompt_result = self._prompt_builder.run(  # type: ignore[union-attr]
                documents=documents,
                query=question,
                history=history or None,
            )
            messages = prompt_result["messages"]
        except Exception as e:
            logger.error("Prompt build failed: %s", e)
            yield ErrorEvent(message=f"Prompt build failed: {e}")
            return

        # ── 11. Queue notification ────────────────────────────────────────────
        if llm_semaphore.locked():
            yield QueuedEvent()

        # ── 12. Streaming LLM ─────────────────────────────────────────────────
        # Resolve sampling params: DB profile → built-in defaults → env config fallback.
        # Each param resolved independently; None in profile = inherit from next level.
        if model_profile_resolver is not None:
            profile = await model_profile_resolver.resolve(effective_model, tenant_id)
        else:
            from ..components.model_profile_resolver import ModelSamplingParams
            profile = ModelSamplingParams()

        def _p(profile_val, cfg_val):  # noqa: E306 — local helper, not a class
            return profile_val if profile_val is not None else cfg_val

        if effective_thinking:
            llm_temperature = _p(profile.thinking_temperature, cfg.llm_thinking_temperature)
            llm_top_p: float | None = _p(profile.thinking_top_p, cfg.llm_thinking_top_p)
            max_tokens = _p(profile.thinking_max_tokens, cfg.llm_thinking_max_tokens)
            frequency_penalty_resolved = _p(profile.thinking_frequency_penalty, cfg.llm_thinking_frequency_penalty)
            presence_penalty_resolved: float | None = _p(profile.thinking_presence_penalty, cfg.llm_thinking_presence_penalty)
            repetition_penalty_resolved: float | None = _p(profile.thinking_repetition_penalty, cfg.llm_thinking_repetition_penalty)
            top_k_resolved: int | None = _p(profile.thinking_top_k, cfg.llm_thinking_top_k)
            min_p_resolved: float | None = _p(profile.thinking_min_p, cfg.llm_thinking_min_p)
            thinking_budget_resolved: int | None = _p(profile.thinking_budget, cfg.llm_thinking_budget)
            stream_thinking_resolved: bool = _p(profile.stream_thinking, cfg.llm_stream_thinking)
        else:
            llm_temperature = _p(profile.temperature, cfg.llm_temperature)
            llm_top_p = _p(profile.top_p, cfg.llm_top_p)
            max_tokens = _p(profile.max_tokens, cfg.llm_max_tokens)
            frequency_penalty_resolved = _p(profile.frequency_penalty, cfg.llm_frequency_penalty)
            presence_penalty_resolved = _p(profile.presence_penalty, cfg.llm_presence_penalty) or None
            repetition_penalty_resolved = _p(profile.repetition_penalty, cfg.llm_repetition_penalty)
            top_k_resolved = _p(profile.top_k, cfg.llm_top_k)
            min_p_resolved = _p(profile.min_p, cfg.llm_min_p)
            thinking_budget_resolved = None   # never sent in non-thinking mode
            stream_thinking_resolved = False  # never sent in non-thinking mode

        queue: asyncio.Queue = asyncio.Queue()
        llm_error: list = []

        def streaming_callback(chunk):
            # LMForge lifecycle status events — only enqueue the status signal when
            # the chunk carries no content/reasoning (it's a pure lifecycle event).
            # If LMForge includes the status field on content chunks (observed in
            # v0.1.1 for call2 answer tokens), we must NOT return early — let the
            # content branch below process the actual token.
            lmf_status = extract_lmforge_status(chunk)
            has_reasoning = bool(extract_reasoning_content(chunk)) if effective_thinking else False
            has_content = bool(chunk.content)
            if lmf_status and not has_content and not has_reasoning:
                loop.call_soon_threadsafe(queue.put_nowait, ("status", lmf_status))
                return

            # Gate reasoning on effective_thinking — some engines leak reasoning_content
            # even when think:false, so we filter client-side as defence-in-depth.
            if effective_thinking:
                reasoning = extract_reasoning_content(chunk)
                if reasoning:
                    loop.call_soon_threadsafe(queue.put_nowait, ("thinking", reasoning))
                    return
            if chunk.content:
                loop.call_soon_threadsafe(queue.put_nowait, ("answer", chunk.content))

        llm = build_streaming_generator(
            model=effective_model,
            chat_url=cfg.llm_chat_url,
            api_key=cfg.llm_api_key,
            streaming_callback=streaming_callback,
            think=effective_thinking,
            max_tokens=max_tokens,
            temperature=llm_temperature,
            top_p=llm_top_p,
            frequency_penalty=frequency_penalty_resolved,
            presence_penalty=presence_penalty_resolved,
            repetition_penalty=repetition_penalty_resolved,
            top_k=top_k_resolved,
            min_p=min_p_resolved,
            thinking_budget=thinking_budget_resolved,
            stream_reasoning_deltas=stream_thinking_resolved if effective_thinking else None,
            timeout=cfg.llm_thinking_stream_timeout_s if effective_thinking else cfg.llm_stream_timeout_s,
            max_retries=cfg.llm_thinking_stream_max_retries if effective_thinking else cfg.llm_stream_max_retries,
        )

        async def run_llm():
            async with llm_semaphore:
                try:
                    await loop.run_in_executor(None, lambda: llm.run(messages=messages))
                except asyncio.CancelledError:
                    logger.info("LLM task cancelled (client disconnected)")
                except Exception as e:
                    logger.error("Streaming LLM failed: %s", e)
                    llm_error.append(e)
                finally:
                    queue.put_nowait(None)

        full_thinking_parts: list[str] = []
        full_answer_parts: list[str] = []
        task = asyncio.create_task(run_llm())

        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                kind, text = item
                if kind == "thinking":
                    full_thinking_parts.append(text)
                    yield ThinkingTokenEvent(text=text)
                elif kind == "status":
                    # UX-only lifecycle signal — not accumulated into answer/thinking.
                    # "call2_prefill" → LMForge is prefilling Call 2 KV cache.
                    yield StatusEvent(stage="generating_answer" if text == "call2_prefill" else text)
                else:
                    full_answer_parts.append(text)
                    yield TokenEvent(text=text)
        except GeneratorExit:
            task.cancel()
            logger.info("Client disconnected mid-stream, LLM task cancelled")
            return

        await task

        if llm_error and not full_answer_parts and not full_thinking_parts:
            yield ErrorEvent(message=f"LLM generation failed: {llm_error[0]}")
            return

        answer = "".join(full_answer_parts).strip()

        # Heuristic: flag when the reasoning hit ≥90% of the budget.
        # Approximate token count as chars/4.  Stored on self for the stream
        # caller (main.py) to read after iteration.
        if effective_thinking and thinking_budget_resolved:
            thinking_chars = sum(len(t) for t in full_thinking_parts)
            self._last_thinking_truncated = (thinking_chars / 4) >= 0.9 * thinking_budget_resolved
        else:
            self._last_thinking_truncated = False

        # ── 13. Build sources ─────────────────────────────────────────────────
        sources = _build_sources(documents)

        # ── 14. Cache write (fire-and-forget) ─────────────────────────────────
        if use_cache and self._cache_writer and answer:
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

        # ── 15. Persist conversation (fire-and-forget) ────────────────────────
        if conversation_id:
            _cid = conversation_id
            _q = question
            _a = answer
            _s = sources
            _tid = tenant_id
            _summ = summarizer

            async def _persist():
                from ..db import add_message as _add
                try:
                    await _run_db(lambda: _add(_cid, "user", _q, tenant_id=_tid))
                    await _run_db(lambda: _add(_cid, "assistant", _a, tenant_id=_tid, sources=_s))
                    if _summ:
                        await self._maybe_compress_history(_cid, _tid, _summ)
                except Exception as e:
                    logger.warning("Failed to persist conversation: %s", e)
            asyncio.create_task(_persist())

        # ── 16. Sources ───────────────────────────────────────────────────────
        yield SourcesEvent(sources=sources, done=True)

    # ── Aggregating wrapper (non-streaming /query endpoint) ──────────────────

    async def query(
        self,
        *,
        question: str,
        tenant_id: str,
        user_context: UserContext,
        user_roles: list[str] | None = None,
        user_id: str | None = None,
        history: list[dict] | None = None,
        context_state: dict | None = None,
        document_type: str | None = None,
        top_k: int | None = None,
        min_score: float | None = None,
        use_cache: bool = True,
        use_reranking: bool = True,
        effective_model: str,
        effective_thinking: bool = False,
        settings: Settings,
        llm_semaphore: asyncio.Semaphore,
        request_id: str,
        model_profile_resolver: ModelProfileResolver | None = None,
        conversation_id: str | None = None,
        summarizer=None,
    ) -> dict:
        """
        Drain stream() into the legacy dict shape consumed by /query.
        """
        start_time = time.time()
        full_thinking: list[str] = []
        full_answer: list[str] = []
        sources: list[dict] = []
        cache_hit = False
        detected_domain: str | None = None

        async for event in self.stream(
            question=question,
            tenant_id=tenant_id,
            user_context=user_context,
            user_roles=user_roles,
            user_id=user_id,
            history=history,
            context_state=context_state,
            document_type=document_type,
            top_k=top_k,
            min_score=min_score,
            use_cache=use_cache,
            use_reranking=use_reranking,
            effective_model=effective_model,
            effective_thinking=effective_thinking,
            settings=settings,
            llm_semaphore=llm_semaphore,
            request_id=request_id,
            model_profile_resolver=model_profile_resolver,
            conversation_id=conversation_id,
            summarizer=summarizer,
        ):
            match event:
                case MetadataEvent(cache_hit=ch):
                    cache_hit = ch
                case RoutingEvent(domain=d):
                    detected_domain = d
                case ThinkingTokenEvent(text=t):
                    full_thinking.append(t)
                case TokenEvent(text=t):
                    full_answer.append(t)
                case SourcesEvent(sources=s):
                    sources = list(s)
                case ErrorEvent(message=m):
                    raise RuntimeError(m)
                case QueuedEvent():
                    pass
                case StatusEvent():
                    pass  # UX-only signal — not relevant in non-streaming path

        return {
            "answer": "".join(full_answer).strip(),
            "thinking": "".join(full_thinking).strip(),
            "sources": sources,
            "cache_hit": cache_hit,
            "latency_ms": int((time.time() - start_time) * 1000),
            "model_used": effective_model,
            "detected_domain": detected_domain,
            "thinking_truncated": getattr(self, "_last_thinking_truncated", False),
        }
