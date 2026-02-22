"""
Query Pipeline
==============

Haystack pipeline for RAG query processing with domain-aware routing.

Flow:
  Query → Embedder → Cache Check → SecureRetriever → Reranker → LLM
"""

from haystack import Pipeline
from haystack.components.embedders import SentenceTransformersTextEmbedder
from haystack.components.rankers import TransformersSimilarityRanker
from haystack.components.joiners import DocumentJoiner
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore
import os
from typing import Optional
import time

from haystack.dataclasses import ChatMessage
from haystack_integrations.components.generators.ollama import OllamaChatGenerator

from ..components import (
    SemanticCacheChecker,
    SecureRetriever,
    PromptBuilder,
    QueryExpander,
)


# Configuration
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
LLM_MODEL = os.getenv("LITELLM_MODEL", "ollama/qwen3:4b")
LLM_FALLBACKS = os.getenv("LITELLM_FALLBACKS", "ollama/phi3:mini").split(",")
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


def _ollama_model_name(model: str) -> str:
    """Strip ollama/ prefix for OllamaChatGenerator."""
    return model.replace("ollama/", "") if model.startswith("ollama/") else model
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

# RAG Pipeline Configuration (internal, not exposed to UI)
RETRIEVER_TOP_K = int(os.getenv("RAG_RETRIEVER_TOP_K", "50"))
RERANKER_TOP_K = int(os.getenv("RAG_RERANKER_TOP_K", "10"))
DEFAULT_TOP_K = int(os.getenv("RAG_DEFAULT_TOP_K", "5"))


def _build_section_label(meta: dict, chunk_index: int) -> str:
    """Build citation label from metadata: p. N, sample X doc Y, or chunk N."""
    if meta.get("page") is not None:
        return f"p. {meta['page']}"
    item_idx = meta.get("item_index")
    doc_idx = meta.get("doc_index")
    if item_idx is not None and doc_idx is not None:
        return f"sample {item_idx}, doc {doc_idx}"
    return f"chunk {chunk_index}"


class RAGQueryPipeline:
    """
    Complete RAG query pipeline with caching, retrieval, and generation.

    Simplified flow (without TransformersZeroShotTextRouter for now):
      Query → QueryExpander → Embedder → CacheCheck → Retriever → Reranker → LLM
    """

    def __init__(
        self,
        qdrant_url: Optional[str] = None,
        embedding_model: Optional[str] = None,
        reranker_model: Optional[str] = None,
        llm_model: Optional[str] = None,
        use_cache: bool = True,
        use_reranking: bool = True,
        use_query_expansion: bool = False,  # Disabled by default for latency
    ):
        self.qdrant_url = qdrant_url or QDRANT_URL
        self.embedding_model = embedding_model or EMBEDDING_MODEL
        self.reranker_model = reranker_model or RERANKER_MODEL
        self.llm_model = llm_model or LLM_MODEL
        self.use_cache = use_cache
        self.use_reranking = use_reranking
        self.use_query_expansion = use_query_expansion

        self._pipeline: Optional[Pipeline] = None
        self._embedder: Optional[SentenceTransformersTextEmbedder] = None
        self._cache_checker: Optional[SemanticCacheChecker] = None
        self._retriever: Optional[SecureRetriever] = None
        self._reranker: Optional[TransformersSimilarityRanker] = None
        self._llm: Optional[OllamaChatGenerator] = None

    def _initialize(self):
        """Lazy initialization of pipeline components."""
        if self._pipeline is not None:
            return

        # Initialize embedder (shared for query and cache)
        self._embedder = SentenceTransformersTextEmbedder(
            model=self.embedding_model,
            trust_remote_code=True,
        )
        # Warm up embedder
        self._embedder.warm_up()

        # Initialize cache checker
        if self.use_cache:
            self._cache_checker = SemanticCacheChecker(qdrant_url=self.qdrant_url)

        # Initialize retriever
        self._retriever = SecureRetriever(
            qdrant_url=self.qdrant_url,
            collection="documents",
            top_k=RETRIEVER_TOP_K,
        )

        # Initialize reranker
        if self.use_reranking:
            self._reranker = TransformersSimilarityRanker(
                model=self.reranker_model,
                top_k=RERANKER_TOP_K,
            )
            self._reranker.warm_up()

        # Initialize LLM (OllamaChatGenerator with structured output)
        self._llm = OllamaChatGenerator(
            model=_ollama_model_name(self.llm_model),
            url=OLLAMA_URL,
            response_format="json",
            generation_kwargs={"temperature": 0.1, "num_predict": 1024},
        )

        # Initialize query expander
        if self.use_query_expansion:
            self._query_expander = QueryExpander(enabled=True)

        # Build pipeline
        self._pipeline = Pipeline()
        self._prompt_builder = PromptBuilder()

    def run(
        self,
        question: str,
        tenant_id: str,
        user_roles: Optional[list[str]] = None,
        user_id: Optional[str] = None,
        document_type: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> dict:
        """
        Execute the RAG pipeline.

        Args:
            question: User's question
            tenant_id: Tenant ID for isolation
            user_roles: Optional user roles for ACL filtering
            user_id: Optional user ID for ACL filtering
            document_type: Optional document type filter
            top_k: Number of results to return (uses DEFAULT_TOP_K if not specified)

        Returns:
            dict with 'answer', 'sources', 'cache_hit', 'latency_ms'
        """
        # Use configured default if not specified
        if top_k is None:
            top_k = DEFAULT_TOP_K
        self._initialize()
        start_time = time.time()

        # Step 1: Embed query
        embed_result = self._embedder.run(text=question)
        query_embedding = embed_result["embedding"]

        # Step 2: Check cache
        cache_hit = False
        if self.use_cache and self._cache_checker:
            cache_result = self._cache_checker.run(
                query_embedding=query_embedding,
                tenant_id=tenant_id,
            )
            if cache_result["cache_hit"]:
                latency_ms = int((time.time() - start_time) * 1000)
                raw_sources = cache_result["cached_sources"] or []
                # Ensure ref_id for cached sources (legacy entries may not have it)
                sources = []
                for i, s in enumerate(raw_sources):
                    d = dict(s) if isinstance(s, dict) else {}
                    if "ref_id" not in d:
                        d["ref_id"] = i + 1
                    sources.append(d)
                return {
                    "answer": cache_result["cached_response"],
                    "sources": sources,
                    "cache_hit": True,
                    "latency_ms": latency_ms,
                    "model_used": "cache",
                }

        # Step 3: Build domain filter
        domain_filter = None
        if document_type and document_type != "all":
            domain_filter = {
                "key": "document_type",
                "match": {"value": document_type},
            }

        # Step 4: Retrieve documents
        retrieval_result = self._retriever.run(
            query_embedding=query_embedding,
            tenant_id=tenant_id,
            user_roles=user_roles,
            user_id=user_id,
            domain_filter=domain_filter,
        )
        documents = retrieval_result["documents"]

        if not documents:
            latency_ms = int((time.time() - start_time) * 1000)
            return {
                "answer": "I couldn't find any relevant documents to answer your question.",
                "sources": [],
                "cache_hit": False,
                "latency_ms": latency_ms,
                "model_used": "none",
            }

        # Step 5: Rerank if enabled
        if self.use_reranking and self._reranker and len(documents) > 1:
            rerank_result = self._reranker.run(documents=documents, query=question)
            documents = rerank_result["documents"][:top_k]
        else:
            documents = documents[:top_k]

        # Step 6: Build prompt
        prompt_result = self._prompt_builder.run(documents=documents, query=question)
        prompt = prompt_result["prompt"]

        # Step 7: Generate answer (structured JSON with answer field)
        messages = [ChatMessage.from_user(prompt)]
        llm_result = self._llm.run(messages=messages)
        reply = llm_result["replies"][0]
        raw_text = getattr(reply, "text", None) or (
            reply.content[0].text if reply.content else ""
        )
        model_used = getattr(reply, "meta", {}) or {}
        if isinstance(model_used, dict):
            model_used = model_used.get("model", self.llm_model)
        else:
            model_used = self.llm_model

        # Parse JSON response; fallback to raw text
        answer = raw_text
        try:
            import json

            parsed = json.loads(raw_text.strip())
            if isinstance(parsed, dict) and "answer" in parsed:
                answer = parsed["answer"]
        except (json.JSONDecodeError, TypeError):
            pass

        # Build sources list with ref_id for [1], [2] mapping
        sources = []
        for i, doc in enumerate(documents):
            chunk_idx = doc.meta.get("chunk_index", i)
            section = _build_section_label(doc.meta, chunk_idx)
            sources.append({
                "ref_id": i + 1,
                "chunk_id": doc.id,
                "document_id": doc.meta.get("document_id", ""),
                "filename": doc.meta.get("filename", "Unknown"),
                "section": section,
                "content": doc.content[:200] + "..." if len(doc.content) > 200 else doc.content,
                "score": doc.score or 0.0,
                "metadata": {k: v for k, v in doc.meta.items() if k not in ["content"]},
            })

        latency_ms = int((time.time() - start_time) * 1000)

        return {
            "answer": answer,
            "sources": sources,
            "cache_hit": cache_hit,
            "latency_ms": latency_ms,
            "model_used": model_used,
        }


# Global pipeline instance
_query_pipeline: Optional[RAGQueryPipeline] = None


def get_query_pipeline() -> RAGQueryPipeline:
    """Get or create global query pipeline instance."""
    global _query_pipeline
    if _query_pipeline is None:
        _query_pipeline = RAGQueryPipeline()
    return _query_pipeline
