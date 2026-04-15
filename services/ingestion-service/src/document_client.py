"""
HTTP client for document-service internal API.

Replaces the direct psycopg2/SQLAlchemy writes that ingestion-service
used to perform against the PostgreSQL chunks and documents tables.
All data mutations for document ownership (chunks, status) now go through
document-service, which is the single owner of the documents schema.
"""

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from docintel_common.internal_auth import compute_service_token
from docintel_common.tracing import TraceContext

from .config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


@dataclass
class ChunkPayload:
    chunk_id: str
    chunk_index: int
    content: str
    start_char: int = 0
    end_char: int = 0
    token_count: int = 0
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return {
            "chunkId":    self.chunk_id,
            "chunkIndex": self.chunk_index,
            "content":    self.content,
            "startChar":  self.start_char,
            "endChar":    self.end_char,
            "tokenCount": self.token_count,
            "metadata":   self.metadata or {},
        }


class DocumentServiceClient:
    """Thin HTTP client for document-service internal endpoints."""

    def __init__(self, base_url: str | None = None) -> None:
        cfg = get_settings()
        self._base = (base_url or cfg.document_service_url).rstrip("/")
        self._internal_secret = cfg.internal_gateway_secret

    def _headers(self, tenant_id: str) -> dict[str, str]:
        if not self._internal_secret:
            raise RuntimeError(
                "INTERNAL_GATEWAY_SECRET is not set — refusing to make unauthenticated "
                "internal service call (fail-secure)"
            )
        token = compute_service_token(tenant_id, self._internal_secret)
        headers: dict[str, str] = {
            "X-Tenant-Id":              tenant_id,
            "Content-Type":             "application/json",
            "X-Internal-Service-Token": token,
        }
        request_id = TraceContext.get_request_id()
        if request_id and request_id != "-":
            headers["X-Request-Id"] = request_id
        return headers

    def persist_chunks(
        self,
        document_id: str,
        tenant_id: str,
        chunks: list[ChunkPayload],
    ) -> None:
        """POST /internal/documents/{id}/chunks/bulk — synchronous (called from thread pool)."""
        if not chunks:
            return

        url = f"{self._base}/internal/documents/{document_id}/chunks/bulk"
        payload = [c.to_dict() for c in chunks]

        try:
            resp = httpx.post(
                url,
                json=payload,
                headers=self._headers(tenant_id),
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            result = resp.json()
            logger.info(
                "Persisted %d chunks for document %s via document-service (saved=%s)",
                len(chunks), document_id, result.get("saved"),
            )
        except httpx.HTTPStatusError as e:
            logger.error(
                "document-service bulk chunk persist failed: status=%s body=%s",
                e.response.status_code, e.response.text,
            )
            raise
        except Exception as e:
            logger.error("document-service bulk chunk persist error: %s", e)
            raise
