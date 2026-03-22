"""
OPA Chunk Validator Component
==============================

Post-retrieval chunk-level ABAC enforcement via Open Policy Agent.

After SecureRetriever returns candidate chunks (which already pass Qdrant-level
coarse ACL filtering), this component makes a per-chunk call to OPA's
docintel.chunk policy for fine-grained ABAC evaluation. It fails closed:
if OPA is unreachable or returns an error, the chunk is denied.

A RetrievalAuditEvent is emitted as a structured log entry for every call,
recording which chunks were retrieved, which were denied, and why.

Pipeline position:
  SecureRetriever → OpaChunkValidator → InfinityReranker → PromptBuilder
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from haystack import Document, component

from docintel_common.security import RetrievalAuditEvent, UserContext

logger = logging.getLogger(__name__)


@component
class OpaChunkValidator:
    """
    Validates each retrieved chunk against OPA docintel.chunk policy.

    Fail-closed: any OPA error / timeout → chunk denied.
    Audit log: one structured RetrievalAuditEvent per call.
    """

    def __init__(self, opa_url: str = "http://opa:8181"):
        self._opa_url = opa_url.rstrip("/")
        self._endpoint = f"{self._opa_url}/v1/data/docintel/chunk/allow"
        # Synchronous client — OPA calls are fast (<5ms on localhost)
        self._client = httpx.Client(timeout=1.0)

    @component.output_types(documents=list[Document])
    def run(
        self,
        documents: list[Document],
        user_context: UserContext,
        request_id: Optional[str] = None,
    ) -> dict:
        """
        Evaluate chunk access for each document in the list.

        Parameters:
            documents:    retrieved chunks from SecureRetriever
            user_context: caller identity + permissions
            request_id:   correlation ID for audit log (optional)

        Returns:
            {"documents": <allowed chunks only>}
        """
        start_ns = time.perf_counter_ns()
        req_id = request_id or str(uuid.uuid4())

        allowed: list[Document] = []
        denied_ids: list[str] = []
        doc_ids: list[str] = []

        user_dict = {
            "user_id":    user_context.user_id,
            "org_id":     user_context.org_id,
            "tenant_id":  user_context.tenant_id,
            "roles":      user_context.roles,
            "clearance":  user_context.clearance.value,
            "department": user_context.department,
            "region":     user_context.region,
        }

        for doc in documents:
            chunk_meta = doc.meta or {}
            doc_id = chunk_meta.get("document_id", "")
            if doc_id and doc_id not in doc_ids:
                doc_ids.append(doc_id)

            try:
                payload = {
                    "input": {
                        "user":  user_dict,
                        "chunk": {
                            "classification": chunk_meta.get("classification", "internal"),
                            "allowed_roles":  chunk_meta.get("allowed_roles", []),
                            "allowed_users":  chunk_meta.get("allowed_users", []),
                            "department":     chunk_meta.get("department"),
                            "region":         chunk_meta.get("region", "global"),
                            "expires_at":     chunk_meta.get("expires_at"),
                        },
                    }
                }
                resp = self._client.post(self._endpoint, json=payload)
                if resp.status_code == 200 and resp.json().get("result") is True:
                    allowed.append(doc)
                else:
                    denied_ids.append(doc.id or "")
                    logger.debug(
                        "OPA denied chunk: doc_id=%s chunk_id=%s classification=%s",
                        doc_id, doc.id, chunk_meta.get("classification"),
                    )
            except Exception as exc:
                # Fail-closed: network error / timeout → deny chunk
                denied_ids.append(doc.id or "")
                logger.warning("OPA unreachable — chunk denied (fail-closed): %s", exc)

        latency_ms = (time.perf_counter_ns() - start_ns) // 1_000_000

        # Emit structured audit event
        audit = RetrievalAuditEvent(
            request_id=req_id,
            user_id=user_context.user_id,
            org_id=user_context.org_id,
            tenant_id=user_context.tenant_id,
            query="",  # not available at this stage; filled upstream if needed
            retrieved_chunk_ids=[d.id or "" for d in documents],
            denied_chunk_ids=denied_ids,
            document_ids=doc_ids,
            timestamp=datetime.now(timezone.utc),
            latency_ms=latency_ms,
        )
        logger.info("audit %s", audit.model_dump_json())

        return {"documents": allowed}

    def close(self) -> None:
        self._client.close()


__all__ = ["OpaChunkValidator"]
