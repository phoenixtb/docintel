"""
Reranker components for Haystack pipelines.

Two implementations:

LocalCrossEncoderRanker (default)
  In-process cross-encoder via sentence-transformers.
  Device auto-selection: mps (Apple Silicon) → cuda (NVIDIA) → cpu.
  No external service required. Model downloaded once and cached by HuggingFace Hub.

InfinityReranker (optional, for NVIDIA TensorRT deployments)
  Calls the Infinity server's Cohere-compatible /rerank endpoint.
  Activate via: docker compose --profile infinity up
"""

import logging
from typing import Any, Optional

import httpx
from haystack import Document, component

logger = logging.getLogger(__name__)


@component
class LocalCrossEncoderRanker:
    """
    In-process cross-encoder reranker using sentence-transformers.

    Lazy-loads the model on first use (or explicit warm_up call).
    Device is auto-selected: mps → cuda → cpu.

    Args:
        model:   HuggingFace cross-encoder model name.
        top_k:   Max documents to return after reranking.
    """

    def __init__(
        self,
        model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        top_k: int = 10,
    ):
        self.model_name = model
        self.top_k = top_k
        self._encoder = None

    def warm_up(self):
        import torch
        from sentence_transformers import CrossEncoder

        if torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
        else:
            device = "cpu"

        logger.info("Loading CrossEncoder %s on device=%s", self.model_name, device)
        self._encoder = CrossEncoder(self.model_name, device=device)

    @component.output_types(documents=list[Document])
    def run(
        self,
        query: str,
        documents: list[Document],
        top_k: Optional[int] = None,
    ) -> dict[str, Any]:
        if not documents:
            return {"documents": []}

        effective_top_k = top_k or self.top_k

        if self._encoder is None:
            logger.warning("LocalCrossEncoderRanker not warmed up — loading now")
            self.warm_up()

        pairs = [(query, doc.content or "") for doc in documents]
        scores = self._encoder.predict(pairs)

        scored = []
        for doc, score in zip(documents, scores):
            scored.append(
                Document(
                    id=doc.id,
                    content=doc.content,
                    meta=doc.meta,
                    score=float(score),
                    embedding=doc.embedding,
                    sparse_embedding=doc.sparse_embedding,
                )
            )

        scored.sort(key=lambda d: d.score or 0.0, reverse=True)
        return {"documents": scored[:effective_top_k]}


@component
class InfinityReranker:
    """
    Haystack component that calls an Infinity server to rerank documents.

    Infinity exposes a Cohere-compatible POST /rerank endpoint. Scores are
    written to Document.score; documents are returned in descending score order.

    Args:
        url:    Base URL of the Infinity server (e.g. "http://infinity:7997").
        model:  Reranker model name served by Infinity.
        top_k:  Maximum number of documents to return after reranking.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        url: str = "http://infinity:7997",
        model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        top_k: int = 10,
        timeout: float = 30.0,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.top_k = top_k
        self.timeout = timeout

    @component.output_types(documents=list[Document])
    def run(
        self,
        query: str,
        documents: list[Document],
        top_k: Optional[int] = None,
    ) -> dict[str, Any]:
        if not documents:
            return {"documents": []}

        effective_top_k = top_k or self.top_k

        doc_texts = [doc.content or "" for doc in documents]

        try:
            response = httpx.post(
                f"{self.url}/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": doc_texts,
                    "top_n": effective_top_k,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            results = response.json().get("results", [])

            scored: list[Document] = []
            for r in results:
                idx = r["index"]
                score = r["relevance_score"]
                doc = documents[idx]
                scored.append(
                    Document(
                        id=doc.id,
                        content=doc.content,
                        meta=doc.meta,
                        score=score,
                        embedding=doc.embedding,
                        sparse_embedding=doc.sparse_embedding,
                    )
                )

            scored.sort(key=lambda d: d.score or 0.0, reverse=True)
            return {"documents": scored}

        except httpx.HTTPError as e:
            logger.error("Infinity reranker request failed: %s — falling back to unranked", e)
            # Graceful degradation: return documents unranked
            for i, doc in enumerate(documents[:effective_top_k]):
                doc.score = doc.score or 0.0
            return {"documents": documents[:effective_top_k]}
