"""
Indexing Pipeline
=================

Haystack Pipeline for embedding and storing document chunks.

Flow: Documents → BM25SparseDocumentEmbedder → OllamaDocumentEmbedder → DocumentWriter

Both dense (nomic-embed-text via Ollama) and sparse (BM25 via fastembed) embeddings are
produced and stored in Qdrant. QdrantHybridRetriever fuses both at query time via RRF.
Dense embedding runs on Ollama (Metal/GPU on Apple Silicon).
"""

import logging
import uuid
from typing import Optional

from haystack import Document, Pipeline
from haystack.components.writers import DocumentWriter
from haystack_integrations.components.embedders.ollama import OllamaDocumentEmbedder
from qdrant_client import QdrantClient, models

from ..components.cache import invalidate_cache_for_tenant
from ..components.embedders import BM25SparseDocumentEmbedder
from ..config import Settings, get_settings
from ..stores import create_document_store

logger = logging.getLogger(__name__)


def create_indexing_pipeline(settings: Settings | None = None) -> Pipeline:
    """
    Build and return the indexing pipeline.

    Flow: BM25SparseDocumentEmbedder → OllamaDocumentEmbedder → DocumentWriter
    """
    cfg = settings or get_settings()
    document_store = create_document_store(cfg)

    pipeline = Pipeline()
    pipeline.add_component("sparse_embedder", BM25SparseDocumentEmbedder())
    pipeline.add_component(
        "embedder",
        OllamaDocumentEmbedder(
            model=cfg.ollama_embed_model,
            url=cfg.ollama_base_url,
        ),
    )
    pipeline.add_component("writer", DocumentWriter(document_store=document_store))

    pipeline.connect("sparse_embedder.documents", "embedder.documents")
    pipeline.connect("embedder.documents", "writer.documents")

    return pipeline


async def index_chunks(
    chunks: list[dict],
    tenant_id: str,
    document_id: str,
    settings: Settings | None = None,
    pipeline: Optional[Pipeline] = None,
) -> dict:
    """
    Index document chunks with dense + BM25 sparse embeddings.

    Args:
        chunks:      List of dicts with 'content' and 'metadata' keys.
        tenant_id:   Tenant ID for isolation.
        document_id: Document ID.
        settings:    Optional Settings (defaults to get_settings()).
        pipeline:    Optional pre-built pipeline (created if not provided).

    Returns:
        {'embedded_count': int, 'collection': str}
    """
    cfg = settings or get_settings()
    if pipeline is None:
        pipeline = create_indexing_pipeline(cfg)

    documents = [
        Document(
            id=chunk.get("chunk_id", str(uuid.uuid4())),
            content=chunk["content"],
            meta={
                "tenant_id": tenant_id,
                "document_id": document_id,
                **chunk.get("metadata", {}),
            },
        )
        for chunk in chunks
    ]

    result = pipeline.run({"sparse_embedder": {"documents": documents}})

    try:
        invalidate_cache_for_tenant(tenant_id, qdrant_url=cfg.qdrant_url)
    except Exception as e:
        logger.warning("Cache invalidation failed for tenant %s: %s", tenant_id, e)

    return {
        "embedded_count": result["writer"]["documents_written"],
        "collection": cfg.qdrant_collection,
    }


async def delete_document_vectors(
    document_id: str,
    tenant_id: str,
    settings: Settings | None = None,
) -> dict:
    """Delete all vectors for a specific document."""
    cfg = settings or get_settings()
    client = QdrantClient(url=cfg.qdrant_url)
    client.delete(
        collection_name=cfg.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    ),
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=document_id),
                    ),
                ]
            )
        ),
    )
    return {"deleted": True, "document_id": document_id}


async def delete_tenant_vectors(
    tenant_id: str,
    settings: Settings | None = None,
) -> dict:
    """Delete all vectors for a tenant."""
    cfg = settings or get_settings()
    client = QdrantClient(url=cfg.qdrant_url)
    client.delete(
        collection_name=cfg.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    ),
                ]
            )
        ),
    )
    return {"deleted": True, "tenant_id": tenant_id}
