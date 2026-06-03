"""
Qdrant document store factory for the ingestion-service.

Creates per-tenant collections with hybrid search (dense + sparse) support.
ACL payload indexes are created on every new collection to enable efficient
chunk-level ABAC filtering by OpaChunkValidator and SecureRetriever.
"""

import logging

from haystack_integrations.document_stores.qdrant import QdrantDocumentStore
from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

_store_cache: dict[str, QdrantDocumentStore] = {}

# ACL fields stored in every point payload; indexed for efficient ABAC filtering.
_ACL_INDEXES: list[tuple[str, qmodels.PayloadSchemaType]] = [
    ("meta.classification",  qmodels.PayloadSchemaType.KEYWORD),
    ("meta.allowed_roles",   qmodels.PayloadSchemaType.KEYWORD),
    ("meta.allowed_users",   qmodels.PayloadSchemaType.KEYWORD),
    ("meta.department",      qmodels.PayloadSchemaType.KEYWORD),
    ("meta.region",          qmodels.PayloadSchemaType.KEYWORD),
    ("meta.expires_at",      qmodels.PayloadSchemaType.DATETIME),
    ("meta.document_id",     qmodels.PayloadSchemaType.KEYWORD),
]


def _ensure_acl_indexes(client: QdrantClient, collection_name: str) -> None:
    """Create ACL payload indexes if they don't already exist."""
    try:
        # payload_schema is a dict[field_name, FieldSchema] — keys are field names.
        info = client.get_collection(collection_name)
        existing: set[str] = set(info.payload_schema.keys()) if hasattr(info, "payload_schema") else set()
    except Exception:
        existing = set()

    for field, schema in _ACL_INDEXES:
        if field in existing:
            continue
        try:
            client.create_payload_index(
                collection_name=collection_name,
                field_name=field,
                field_schema=schema,
            )
            logger.debug("Created payload index: %s.%s", collection_name, field)
        except Exception as e:
            logger.warning("Could not create index %s on %s: %s", field, collection_name, e)


def get_document_store(tenant_id: str, settings: Settings | None = None) -> QdrantDocumentStore:
    """
    Return (or create) the Qdrant document store for a given tenant.

    Collection name: ``documents_{tenant_id}``
    Vector dimension read from ``cfg.llm_embed_dim`` (default 1024 for qwen3-embed).
    Sparse vectors enabled for BM25 hybrid search.
    ACL payload indexes are ensured on every call.
    """
    if tenant_id in _store_cache:
        return _store_cache[tenant_id]

    cfg = settings or get_settings()
    collection_name = f"documents_{tenant_id}"

    store = QdrantDocumentStore(
        url=cfg.qdrant_url,
        api_key=cfg.qdrant_api_key,
        index=collection_name,
        embedding_dim=cfg.llm_embed_dim,
        use_sparse_embeddings=True,
        sparse_idf=True,
        recreate_index=False,
        # gRPC must stay disabled: docling chunk metadata includes xxhash64 IDs
        # (uint64) that exceed protobuf int64 range and crash the gRPC upsert
        # with "Value out of range". HTTP/REST accepts arbitrary JSON ints.
        prefer_grpc=False,
    )

    # Ensure ACL indexes after the store (and collection) is initialized.
    # Reuse the store's internal QdrantClient to avoid opening a second TCP connection.
    # qdrant-haystack 10.x renamed the public `client` attr to a lazily-initialized
    # `_client`; force initialization, then reach into the private attr.
    try:
        store._initialize_client()  # type: ignore[attr-defined]
        internal_client: QdrantClient = store._client  # type: ignore[attr-defined]
        _ensure_acl_indexes(internal_client, collection_name)
    except Exception as e:
        logger.warning("ACL index creation failed (non-fatal): %s", e)

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
    # qdrant-haystack 10.x: filter-based delete moved to delete_by_filter().
    # delete_documents(...) now only accepts a list of point IDs.
    deleted = store.delete_by_filter(
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
        client = QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key)
        client.delete_collection(collection_name)
        _store_cache.pop(tenant_id, None)
        logger.info("Deleted Qdrant collection: %s", collection_name)
    except Exception as e:
        logger.warning("Could not delete Qdrant collection %s: %s", collection_name, e)


def evict_store_cache(tenant_id: str) -> None:
    """Remove a tenant's store from the cache so the next call creates a fresh connection."""
    _store_cache.pop(tenant_id, None)


def invalidate_cache_for_tenant(
    tenant_id: str,
    settings: Settings | None = None,
    collection: str = "response_cache",
) -> dict:
    """
    Invalidate (delete) all cached responses for a tenant.

    Called after document deletion to prevent stale cache hits referencing
    deleted content. Thin wrapper around a direct Qdrant delete — no
    cross-service import required since qdrant-client is already a dependency.
    """
    cfg = settings or get_settings()
    client = QdrantClient(url=cfg.qdrant_url, api_key=cfg.qdrant_api_key)

    collections = client.get_collections().collections
    if not any(c.name == collection for c in collections):
        return {"invalidated": False, "reason": "collection_not_exists"}

    client.delete(
        collection_name=collection,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="tenant_id",
                        match=qmodels.MatchValue(value=tenant_id),
                    )
                ]
            )
        ),
    )
    logger.info("Cache invalidated for tenant: %s", tenant_id)
    return {"invalidated": True, "tenant_id": tenant_id}
