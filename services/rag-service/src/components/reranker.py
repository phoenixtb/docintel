"""
Reranker component for Haystack pipelines.

LmforgeReranker
  Calls LMForge's POST /v1/rerank (contract: docs/contracts/reranker.md).
  Scores are relevance probabilities in [0, 1] (`score_type: "probability"`);
  documents are returned in descending score order.

  Any failure — transport error, an LMForge error code, or a response that does
  not declare probability scores — yields `reranker_degraded=True` with a
  `degraded_reason`, and the documents in their retrieval (fused) order. Callers
  must not apply a reranker-calibrated threshold to those fused scores.
"""

import logging
from typing import Any

import httpx
from haystack import Document, component

logger = logging.getLogger(__name__)

# LMForge error codes (docs/contracts/reranker.md); anything else is reported as http_<status>.
_KNOWN_ERROR_CODES = {"query_too_long", "input_too_long", "reranker_unusable"}


def _error_code(response: httpx.Response) -> str:
    """LMForge errors look like {"error": {"message", "type", "param", "code"}}."""
    try:
        code = (response.json().get("error") or {}).get("code")
    except ValueError:
        code = None
    return code if code in _KNOWN_ERROR_CODES else f"http_{response.status_code}"


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
        model: str = "qwen3-reranker:0.6b:8bit",
        top_k: int = 10,
        timeout: float = 30.0,
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.top_k = top_k
        self.timeout = timeout

    @component.output_types(
        documents=list[Document], reranker_degraded=bool, degraded_reason=str | None
    )
    def run(
        self,
        query: str,
        documents: list[Document],
        top_k: int | None = None,
    ) -> dict[str, Any]:
        if not documents:
            return {"documents": [], "reranker_degraded": False, "degraded_reason": None}

        effective_top_k = top_k or self.top_k

        try:
            response = httpx.post(
                f"{self.url}/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": [doc.content or "" for doc in documents],
                    "top_n": effective_top_k,
                },
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            return self._degraded(documents, effective_top_k, "unavailable", str(e))

        if response.status_code != 200:
            return self._degraded(
                documents, effective_top_k, _error_code(response), response.text[:300]
            )

        body = response.json()
        # Older LMForge passed probabilities through a second sigmoid into
        # [0.5, 0.73] and did not declare a scale. Reading those as probabilities
        # would make every threshold wrong, so an undeclared scale is degraded.
        if body.get("score_type") != "probability":
            return self._degraded(
                documents,
                effective_top_k,
                "unsupported_score_contract",
                f"score_type={body.get('score_type')!r}; LMForge with probability scores required",
            )

        truncated = (body.get("meta") or {}).get("truncated_documents") or []
        if truncated:
            logger.info("Reranker truncated %d long document(s): %s", len(truncated), truncated)

        scored: list[Document] = []
        for r in body.get("results", []):
            doc = documents[r["index"]]
            scored.append(
                Document(
                    id=doc.id,
                    content=doc.content,
                    meta=doc.meta,
                    score=r["relevance_score"],
                    embedding=doc.embedding,
                    sparse_embedding=doc.sparse_embedding,
                )
            )
        scored.sort(key=lambda d: d.score or 0.0, reverse=True)
        return {"documents": scored, "reranker_degraded": False, "degraded_reason": None}

    @staticmethod
    def _degraded(
        documents: list[Document], top_k: int, reason: str, detail: str
    ) -> dict[str, Any]:
        logger.error("Reranker degraded (%s): %s — continuing in retrieval order", reason, detail)
        return {
            "documents": list(documents[:top_k]),
            "reranker_degraded": True,
            "degraded_reason": reason,
        }


# Alias kept for backward compatibility during transition
InfinityReranker = LmforgeReranker
