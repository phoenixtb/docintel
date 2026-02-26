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
  │  SecureRetriever → SentenceTransformersSimilarityRanker → PromptBuilder → LLM  │
  └─────────────────────────────────────────────────────────────────────────┘

All model inference (LLM + dense embeddings) runs through Ollama for GPU acceleration.
BM25 sparse embeddings use fastembed locally (no server, lightweight).
Reranker (cross-encoder) runs CPU-local — Haystack has no Ollama reranker component.

Why embedders live outside the pipeline:
  Cache check requires the dense embedding *before* deciding whether to run
  the (expensive) pipeline. Keeping a single embedder instance avoids loading
  the model twice and removes redundant inference.
"""

import json
import logging
import time
from typing import Optional

from haystack import Pipeline
from haystack.components.rankers import SentenceTransformersSimilarityRanker
from haystack.components.routers import TransformersZeroShotTextRouter
from haystack_integrations.components.embedders.ollama import OllamaTextEmbedder

from ..components.cache import SemanticCacheChecker, SemanticCacheWriter
from ..components.embedders import BM25SparseTextEmbedder
from ..components.prompt import PromptBuilder
from ..components.retrieval import SecureRetriever
from ..components.routing import DomainFilterBuilder
from ..config import Settings, get_settings
from ..prompts import DOMAIN_LABELS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------

def build_query_pipeline(settings: Settings) -> Pipeline:
    """
    Build the core RAG Haystack Pipeline.

    Inputs required from caller (pipeline.run input dict):
      retriever:      query_embedding, query_sparse_embedding, tenant_id,
                      user_roles, user_id, domain_filter
      reranker:       query
      prompt_builder: query, history (optional)

    Outputs available after pipeline.run():
      retriever.documents, reranker.documents, llm.replies
    """
    pipeline = Pipeline()

    pipeline.add_component(
        "retriever",
        SecureRetriever(settings=settings),
    )
    pipeline.add_component(
        "reranker",
        SentenceTransformersSimilarityRanker(
            model=settings.reranker_model,
            top_k=settings.rag_reranker_top_k,
            device=None,  # auto: CPU in Docker (no MPS/CUDA passthrough)
        ),
    )
    pipeline.add_component("prompt_builder", PromptBuilder())

    # OllamaChatGenerator is the primary LLM component (proper Haystack integration).
    # Import here to keep the top-level imports clean.
    from haystack_integrations.components.generators.ollama import OllamaChatGenerator

    pipeline.add_component(
        "llm",
        OllamaChatGenerator(
            model=settings.ollama_llm_model,
            url=settings.ollama_base_url,
            generation_kwargs={
                "temperature": settings.ollama_llm_temperature,
                "num_predict": settings.ollama_llm_max_tokens,
            },
        ),
    )

    # Wire component outputs → inputs
    pipeline.connect("retriever.documents", "reranker.documents")
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
    Works on both complete and empty think blocks.
    """
    import re
    m = re.search(r"<think>(.*?)</think>(.*)", raw, re.DOTALL)
    if m:
        return m.group(1).strip(), m.group(2).strip()
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

        # Components managed outside the pipeline
        self._dense_embedder: Optional[OllamaTextEmbedder] = None
        self._sparse_embedder: Optional[BM25SparseTextEmbedder] = None
        self._cache_checker: Optional[SemanticCacheChecker] = None
        self._cache_writer: Optional[SemanticCacheWriter] = None
        self._domain_router: Optional[TransformersZeroShotTextRouter] = None
        self._domain_filter_builder: Optional[DomainFilterBuilder] = None

        # Core Haystack Pipeline
        self._pipeline: Optional[Pipeline] = None

    # ── Initialisation ───────────────────────────────────────────────────────

    def warm_up(self) -> None:
        """
        Initialise and warm up all components.

        Called once at application startup (FastAPI lifespan). Subsequent calls
        are no-ops.
        """
        if self._ready:
            return

        cfg = self._settings

        # Dense embedder — stateless HTTP client to Ollama (Metal/GPU, no warm_up needed)
        self._dense_embedder = OllamaTextEmbedder(
            model=cfg.ollama_embed_model,
            url=cfg.ollama_base_url,
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
        self, conversation_id: str, tenant_id: str, max_messages: int = 10
    ) -> list[dict]:
        try:
            from ..db import get_conversation

            conv = get_conversation(conversation_id, tenant_id)
            if conv and conv.get("messages"):
                msgs = conv["messages"]
                relevant = [m for m in msgs if m["role"] in ("user", "assistant")]
                history = relevant[-(max_messages + 1) : -1] if relevant else []
                return [{"role": m["role"], "content": m["content"]} for m in history]
        except Exception as e:
            logger.warning("Could not load conversation history: %s", e)
        return []

    def _persist_conversation(
        self,
        conversation_id: str,
        question: str,
        answer: str,
        sources: list[dict],
    ) -> None:
        try:
            from ..db import add_message

            add_message(conversation_id, "user", question)
            add_message(conversation_id, "assistant", answer, sources=sources)
        except Exception as e:
            logger.warning("Failed to persist conversation messages: %s", e)

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

    def query(
        self,
        question: str,
        tenant_id: str,
        user_roles: list[str] | None = None,
        user_id: str | None = None,
        document_type: str | None = None,
        top_k: int | None = None,
        conversation_id: str | None = None,
        min_score: float | None = None,
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
        if not self._ready:
            self.warm_up()

        cfg = self._settings
        effective_top_k = top_k if top_k is not None else cfg.rag_default_top_k
        effective_min_score = min_score if min_score is not None else cfg.rag_min_relevance_score
        start_time = time.time()

        # ── Step 1: Embed query ──────────────────────────────────────────────
        embed_result = self._dense_embedder.run(text=question)  # type: ignore[union-attr]
        query_embedding: list[float] = embed_result["embedding"]

        sparse_result = self._sparse_embedder.run(text=question)  # type: ignore[union-attr]
        query_sparse_embedding = sparse_result.get("sparse_embedding")

        # ── Step 2: Cache check ──────────────────────────────────────────────
        if cfg.use_cache and self._cache_checker:
            cache_result = self._cache_checker.run(
                query_embedding=query_embedding,
                tenant_id=tenant_id,
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
            history = self._load_conversation_history(conversation_id, tenant_id)

        # ── Step 5: Core Haystack Pipeline ───────────────────────────────────
        pipeline_inputs = {
            "retriever": {
                "query_embedding": query_embedding,
                "query_sparse_embedding": query_sparse_embedding,
                "tenant_id": tenant_id,
                "user_roles": user_roles,
                "user_id": user_id,
                "domain_filter": domain_filter,
            },
            "reranker": {"query": question},
            "prompt_builder": {
                "query": question,
                "history": history or None,
            },
        }

        result = self._pipeline.run(pipeline_inputs)  # type: ignore[union-attr]

        # ── Step 6: Extract + filter results ─────────────────────────────────
        reranked_docs = result.get("reranker", {}).get("documents", [])

        if effective_min_score > 0.0:
            above = [d for d in reranked_docs if (d.score or 0.0) >= effective_min_score]
            reranked_docs = above if above else reranked_docs[:1]

        documents = reranked_docs[:effective_top_k]

        llm_replies = result.get("llm", {}).get("replies", [])
        raw_text = ""
        model_used = cfg.ollama_llm_model
        if llm_replies:
            reply = llm_replies[0]
            raw_text = getattr(reply, "text", None) or (
                reply.content[0].text if getattr(reply, "content", None) else ""
            )
            meta = getattr(reply, "meta", {}) or {}
            model_used = meta.get("model", cfg.ollama_llm_model) if isinstance(meta, dict) else cfg.ollama_llm_model

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
                "content": doc.content[:200] + "..." if len(doc.content) > 200 else doc.content,
                "score": doc.score or 0.0,
                "metadata": {k: v for k, v in doc.meta.items() if k != "content"},
            })

        # ── Step 8: Cache write ───────────────────────────────────────────────
        if cfg.use_cache and self._cache_writer:
            try:
                self._cache_writer.run(
                    query=question,
                    query_embedding=query_embedding,
                    response=answer,
                    sources=sources,
                    tenant_id=tenant_id,
                )
            except Exception as e:
                logger.warning("Cache write failed: %s", e)

        # ── Step 9: Persist conversation ──────────────────────────────────────
        if conversation_id:
            self._persist_conversation(conversation_id, question, answer, sources)

        return {
            "answer": answer,
            "thinking": thinking,
            "sources": sources,
            "cache_hit": False,
            "latency_ms": latency_ms,
            "model_used": model_used,
            "detected_domain": detected_domain,
        }
