"""
Qdrant document store factory for the ingestion-service.

Creates per-tenant collections with hybrid search (dense + sparse) support.
"""

import logging
from functools import lru_cache

from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

_store_cache: dict[str, QdrantDocumentStore] = {}


def get_document_store(tenant_id: str, settings: Settings | None = None) -> QdrantDocumentStore:
    """
    Return (or create) the Qdrant document store for a given tenant.

    Collection name: ``documents_{tenant_id}``
    Vectors: 768-dimensional (nomic-embed-text default).
    Sparse vectors enabled for BM25 hybrid search.
    """
    if tenant_id in _store_cache:
        return _store_cache[tenant_id]

    cfg = settings or get_settings()
    collection_name = f"documents_{tenant_id}"

    store = QdrantDocumentStore(
        url=cfg.qdrant_url,
        api_key=cfg.qdrant_api_key,
        index=collection_name,
        embedding_dim=768,
        use_sparse_embeddings=True,
        sparse_idf=True,
        recreate_index=False,
    )

    _store_cache[tenant_id] = store
    logger.info("Qdrant store ready: collection=%s", collection_name)
    return store


def delete_document_from_store(
    tenant_id: str,
    document_id: str,
    settings: Settings | None = None,
) -> int:
    """Delete all Qdrant points where meta.document_id == document_id."""
    store = get_document_store(tenant_id, settings)
    deleted = store.delete_documents(
        filters={"field": "meta.document_id", "operator": "==", "value": document_id}
    )
    return deleted or 0


def delete_tenant_from_store(
    tenant_id: str,
    settings: Settings | None = None,
) -> None:
    """Drop the entire Qdrant collection for a tenant."""
    cfg = settings or get_settings()
    collection_name = f"documents_{tenant_id}"
    try:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key)
        client.delete_collection(collection_name)
        _store_cache.pop(tenant_id, None)
        logger.info("Deleted Qdrant collection: %s", collection_name)
    except Exception as e:
        logger.warning("Could not delete Qdrant collection %s: %s", collection_name, e)
