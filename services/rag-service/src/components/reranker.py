"""
Infinity HTTP Reranker — Haystack component.

Calls the Infinity server's Cohere-compatible /rerank endpoint to score and
re-rank retrieved documents. Infinity supports ONNX/TensorRT backends and
can use GPU for cross-encoder inference — far more efficient than running
the model in-process with SentenceTransformersSimilarityRanker.

Infinity API reference: https://github.com/michaelfeil/infinity
"""

import logging
from typing import Any, Optional

import httpx
from haystack import Document, component

logger = logging.getLogger(__name__)


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
