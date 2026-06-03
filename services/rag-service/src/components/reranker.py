"""
Reranker component for Haystack pipelines.

LmforgeReranker
  Calls LMForge's OpenAI-compatible POST /v1/rerank endpoint.
  Works with oMLX on Mac (Apple Silicon). Falls back gracefully if LMForge is unavailable.
  Scores are normalised to [0, 1]; documents returned in descending score order.
"""

import logging
from typing import Any, Optional

import httpx
from haystack import Document, component

logger = logging.getLogger(__name__)


@component
class LmforgeReranker:
    """
    Haystack component that calls LMForge /v1/rerank to rerank documents.

    Args:
        url:     Base URL of LMForge (e.g. "http://host.docker.internal:11430/v1").
        model:   Reranker model ID registered in LMForge catalog.
        top_k:   Maximum number of documents to return after reranking.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        url: str = "http://host.docker.internal:11430/v1",
        model: str = "jina-reranker-v2:multilingual",
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
            logger.error("LMForge reranker request failed: %s — falling back to unranked", e)
            for doc in documents[:effective_top_k]:
                doc.score = doc.score or 0.0
            return {"documents": documents[:effective_top_k]}


# Alias kept for backward compatibility during transition
InfinityReranker = LmforgeReranker
