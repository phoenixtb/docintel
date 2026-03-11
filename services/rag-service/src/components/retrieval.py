"""
Secure Retriever Component
==========================

Wraps QdrantHybridRetriever / QdrantEmbeddingRetriever with ACL filtering.
Builds tenant + role + domain Qdrant filters and delegates to the
Haystack-native retrievers for hybrid dense+sparse (RRF) or dense-only search.

Per-tenant isolation: each tenant's data lives in a separate Qdrant collection
named `documents_{tenant_id}`. Retrievers are cached per tenant so collection
initialisation only happens once per tenant per process lifetime.

Pipeline position:
  [BM25SparseTextEmbedder] → SecureRetriever → [SentenceTransformersSimilarityRanker]
"""

from threading import Lock

from cachetools import LRUCache
from haystack import Document, component
from haystack.dataclasses import SparseEmbedding
from haystack_integrations.components.retrievers.qdrant import (
    QdrantEmbeddingRetriever,
    QdrantHybridRetriever,
)
from qdrant_client import models

from ..config import Settings, get_settings
from ..stores import create_document_store

# LRU cache with a 100-tenant cap to prevent unbounded memory growth.
_retriever_cache: LRUCache = LRUCache(maxsize=100)
_cache_lock = Lock()


def _get_tenant_retrievers(
    tenant_id: str,
    settings: Settings,
    top_k: int,
) -> tuple[QdrantHybridRetriever, QdrantEmbeddingRetriever]:
    """Return cached retrievers for a tenant's collection, creating them on first access."""
    cache_key = f"{tenant_id}:{top_k}"
    with _cache_lock:
        if cache_key not in _retriever_cache:
            store = create_document_store(
                settings,
                collection=f"documents_{tenant_id}",
            )
            _retriever_cache[cache_key] = (
                QdrantHybridRetriever(document_store=store, top_k=top_k),
                QdrantEmbeddingRetriever(document_store=store, top_k=top_k),
            )
        return _retriever_cache[cache_key]


@component
class SecureRetriever:
    """
    Tenant-isolated, ACL-aware retriever.

    Queries the per-tenant Qdrant collection `documents_{tenant_id}`.
    No cross-tenant data can be returned — the collection itself is the isolation boundary.
    Role-based and domain filters are applied on top for fine-grained access control.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        top_k: int | None = None,
        use_hybrid: bool | None = None,
    ):
        self._settings = settings or get_settings()
        self._top_k = top_k if top_k is not None else self._settings.rag_retriever_top_k
        env_hybrid = self._settings.rag_use_hybrid_search
        self._use_hybrid = use_hybrid if use_hybrid is not None else env_hybrid

    def _build_acl_filter(
        self,
        user_roles: list[str] | None,
        user_id: str | None,
        domain_filter: dict | None,
    ) -> models.Filter:
        must: list[models.Condition] = []

        if domain_filter:
            must.append(
                models.FieldCondition(
                    key=f"meta.{domain_filter['key']}",
                    match=models.MatchValue(value=domain_filter["match"]["value"]),
                )
            )

        # should = OR logic: a document is accessible if ANY condition is true.
        # "No allowed_roles" means the document is open to all tenant members —
        # this covers sample datasets and documents indexed without explicit ACL.
        _acl_field = models.PayloadField(key="meta.allowed_roles")
        acl_should: list[models.Condition] = [
            models.IsNullCondition(is_null=_acl_field),
            models.IsEmptyCondition(is_empty=_acl_field),
        ]
        if user_roles:
            acl_should.append(
                models.FieldCondition(
                    key="meta.allowed_roles",
                    match=models.MatchAny(any=user_roles),
                )
            )
        if user_id:
            acl_should.append(
                models.FieldCondition(
                    key="meta.allowed_users",
                    match=models.MatchValue(value=user_id),
                )
            )

        # Nest ACL as a must condition so it is enforced even when a domain
        # must filter is also present (Qdrant's should is optional alongside must).
        must.append(models.Filter(should=acl_should))
        filt = models.Filter(must=must)
        return filt

    @component.output_types(documents=list[Document])
    def run(
        self,
        query_embedding: list[float],
        tenant_id: str,
        user_roles: list[str] | None = None,
        user_id: str | None = None,
        domain_filter: dict | None = None,
        query_sparse_embedding: SparseEmbedding | None = None,
    ) -> dict:
        hybrid_retriever, dense_retriever = _get_tenant_retrievers(
            tenant_id, self._settings, self._top_k
        )
        acl_filter = self._build_acl_filter(user_roles, user_id, domain_filter)

        if self._use_hybrid and query_sparse_embedding is not None:
            result = hybrid_retriever.run(
                query_embedding=query_embedding,
                query_sparse_embedding=query_sparse_embedding,
                filters=acl_filter,
            )
        else:
            result = dense_retriever.run(
                query_embedding=query_embedding,
                filters=acl_filter,
            )

        return {"documents": result["documents"]}


__all__ = ["SecureRetriever"]
