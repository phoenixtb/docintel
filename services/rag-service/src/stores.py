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

    Scalar quantization is enabled to reduce memory usage ~4x with minimal
    accuracy loss. HNSW efConstruct=100 balances index quality vs. build time.

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
        # Scalar quantization: stores vectors as int8 instead of float32
        # Reduces memory ~4x with <5% accuracy loss on typical RAG workloads.
        # Set QDRANT_QUANTIZATION=false in env to disable during benchmarking.
        quantization_config={
            "scalar": {
                "type": "int8",
                "quantile": 0.99,
                "always_ram": True,
            }
        } if cfg.qdrant_quantization else None,
    )
