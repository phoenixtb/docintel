"""
Secure Retriever Component
==========================

Wraps QdrantHybridRetriever / QdrantEmbeddingRetriever with ACL filtering.
Builds tenant + role + domain Qdrant filters and delegates to the
Haystack-native retrievers for hybrid dense+sparse (RRF) or dense-only search.

Pipeline position:
  [BM25SparseTextEmbedder] → SecureRetriever → [TransformersSimilarityRanker]
"""

from haystack import Document, component
from haystack.dataclasses import SparseEmbedding
from haystack_integrations.components.retrievers.qdrant import (
    QdrantEmbeddingRetriever,
    QdrantHybridRetriever,
)
from qdrant_client import models

from ..config import Settings, get_settings
from ..stores import create_document_store


@component
class SecureRetriever:
    """
    Tenant-isolated, ACL-aware retriever.

    Accepts pre-computed dense and sparse embeddings (produced by the service
    layer, shared with the cache check step) and applies Qdrant pre-filters for:
      - tenant isolation  (meta.tenant_id == tenant_id)
      - role-based access (meta.allowed_roles  ANY user_roles)
      - user-level access (meta.allowed_users  == user_id)
      - domain routing    (meta.document_type  == domain)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        top_k: int | None = None,
        use_hybrid: bool | None = None,
    ):
        cfg = settings or get_settings()
        self._top_k = top_k if top_k is not None else cfg.rag_retriever_top_k
        env_hybrid = cfg.rag_use_hybrid_search
        self._use_hybrid = use_hybrid if use_hybrid is not None else env_hybrid

        document_store = create_document_store(cfg)
        self._hybrid_retriever = QdrantHybridRetriever(
            document_store=document_store,
            top_k=self._top_k,
        )
        self._dense_retriever = QdrantEmbeddingRetriever(
            document_store=document_store,
            top_k=self._top_k,
        )

    def _build_acl_filter(
        self,
        tenant_id: str,
        user_roles: list[str] | None,
        user_id: str | None,
        domain_filter: dict | None,
    ) -> models.Filter:
        must: list[models.Condition] = [
            models.FieldCondition(
                key="meta.tenant_id",
                match=models.MatchValue(value=tenant_id),
            )
        ]

        if domain_filter:
            must.append(
                models.FieldCondition(
                    key=f"meta.{domain_filter['key']}",
                    match=models.MatchValue(value=domain_filter["match"]["value"]),
                )
            )

        should: list[models.Condition] = []
        if user_roles:
            should.append(
                models.FieldCondition(
                    key="meta.allowed_roles",
                    match=models.MatchAny(any=user_roles),
                )
            )
        if user_id:
            should.append(
                models.FieldCondition(
                    key="meta.allowed_users",
                    match=models.MatchValue(value=user_id),
                )
            )

        filt = models.Filter(must=must)
        if should:
            filt.should = should
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
        acl_filter = self._build_acl_filter(tenant_id, user_roles, user_id, domain_filter)

        if self._use_hybrid and query_sparse_embedding is not None:
            result = self._hybrid_retriever.run(
                query_embedding=query_embedding,
                query_sparse_embedding=query_sparse_embedding,
                filters=acl_filter,
            )
        else:
            result = self._dense_retriever.run(
                query_embedding=query_embedding,
                filters=acl_filter,
            )

        return {"documents": result["documents"]}


__all__ = ["SecureRetriever"]
