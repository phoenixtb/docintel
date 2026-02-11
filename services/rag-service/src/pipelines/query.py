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

from ..components import (
    LiteLLMGenerator,
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
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

# RAG Pipeline Configuration (internal, not exposed to UI)
RETRIEVER_TOP_K = int(os.getenv("RAG_RETRIEVER_TOP_K", "50"))
RERANKER_TOP_K = int(os.getenv("RAG_RERANKER_TOP_K", "10"))
DEFAULT_TOP_K = int(os.getenv("RAG_DEFAULT_TOP_K", "5"))


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
        self._llm: Optional[LiteLLMGenerator] = None

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

        # Initialize LLM
        self._llm = LiteLLMGenerator(
            model=self.llm_model,
            fallbacks=LLM_FALLBACKS,
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
                return {
                    "answer": cache_result["cached_response"],
                    "sources": cache_result["cached_sources"] or [],
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

        # Step 7: Generate answer
        llm_result = self._llm.run(prompt=prompt)
        answer = llm_result["replies"][0]
        model_used = llm_result["meta"].get("model", self.llm_model)

        # Build sources list
        sources = []
        for doc in documents:
            sources.append({
                "chunk_id": doc.id,
                "document_id": doc.meta.get("document_id", ""),
                "filename": doc.meta.get("filename", "Unknown"),
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
