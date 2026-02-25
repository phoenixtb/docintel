"""
Shared Qdrant document store factory.
Single source of truth for QdrantDocumentStore configuration.
"""

from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from .config import Settings, get_settings


def create_document_store(
    settings: Settings | None = None,
    collection: str | None = None,
) -> QdrantDocumentStore:
    """
    Create QdrantDocumentStore with dense + sparse vector support.

    This is the single factory used by both the indexing pipeline and the
    SecureRetriever — ensures identical collection configuration across both.
    """
    cfg = settings or get_settings()
    return QdrantDocumentStore(
        url=cfg.qdrant_url,
        index=collection or cfg.qdrant_collection,
        embedding_dim=cfg.qdrant_embedding_dim,
        similarity="cosine",
        use_sparse_embeddings=True,
        recreate_index=False,
        hnsw_config={"m": 16, "ef_construct": 100},
        on_disk_payload=True,
    )
