"""
Indexing Pipeline
=================

Haystack pipeline for embedding and storing document chunks.
"""

from haystack import Pipeline, Document
from haystack.components.embedders import SentenceTransformersDocumentEmbedder
from haystack.components.writers import DocumentWriter
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore
from qdrant_client import models
import os
from typing import Optional
import uuid

from ..components.cache import invalidate_cache_for_tenant


# Model configuration
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "documents"


def create_document_store(
    url: Optional[str] = None,
    collection: str = COLLECTION_NAME,
) -> QdrantDocumentStore:
    """Create and configure QdrantDocumentStore."""
    return QdrantDocumentStore(
        url=url or QDRANT_URL,
        index=collection,
        embedding_dim=768,  # nomic-embed-text dimension
        similarity="cosine",
        recreate_index=False,
        hnsw_config={"m": 16, "ef_construct": 100},
        on_disk_payload=True,
    )


def create_indexing_pipeline(document_store: QdrantDocumentStore) -> Pipeline:
    """
    Pipeline for embedding and storing document chunks.

    Flow: Documents → Embedder → Writer
    """
    pipeline = Pipeline()

    pipeline.add_component(
        "embedder",
        SentenceTransformersDocumentEmbedder(
            model=EMBEDDING_MODEL,
            trust_remote_code=True,  # Required for nomic-embed-text
        ),
    )
    pipeline.add_component(
        "writer",
        DocumentWriter(document_store=document_store),
    )

    pipeline.connect("embedder", "writer")
    return pipeline


async def index_chunks(
    chunks: list[dict],
    tenant_id: str,
    document_id: str,
    document_store: Optional[QdrantDocumentStore] = None,
    pipeline: Optional[Pipeline] = None,
) -> dict:
    """
    Index document chunks.

    Args:
        chunks: List of chunk dicts with 'content' and 'metadata' keys
        tenant_id: Tenant ID for isolation
        document_id: Document ID
        document_store: Optional document store (creates new if not provided)
        pipeline: Optional pipeline (creates new if not provided)

    Returns:
        dict with 'embedded_count' and 'collection' keys
    """
    if document_store is None:
        document_store = create_document_store()

    if pipeline is None:
        pipeline = create_indexing_pipeline(document_store)

    # Convert to Haystack Documents
    documents = []
    for chunk in chunks:
        doc = Document(
            id=chunk.get("chunk_id", str(uuid.uuid4())),
            content=chunk["content"],
            meta={
                "tenant_id": tenant_id,
                "document_id": document_id,
                **chunk.get("metadata", {}),
            },
        )
        documents.append(doc)

    # Run pipeline
    result = pipeline.run({"embedder": {"documents": documents}})

    # Invalidate cache for this tenant so new documents are included in search
    try:
        invalidate_cache_for_tenant(tenant_id)
    except Exception as e:
        # Don't fail indexing if cache invalidation fails
        print(f"Warning: Failed to invalidate cache for tenant {tenant_id}: {e}")

    return {
        "embedded_count": result["writer"]["documents_written"],
        "collection": COLLECTION_NAME,
    }


async def delete_document_vectors(
    document_id: str,
    tenant_id: str,
    qdrant_url: Optional[str] = None,
) -> dict:
    """
    Delete all vectors for a specific document.

    Args:
        document_id: Document ID to delete
        tenant_id: Tenant ID for safety check
        qdrant_url: Optional Qdrant URL

    Returns:
        dict with deletion status
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(url=qdrant_url or QDRANT_URL)

    # Delete with filter for safety (both tenant_id and document_id must match)
    client.delete(
        collection_name=COLLECTION_NAME,
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
    qdrant_url: Optional[str] = None,
) -> dict:
    """
    Delete all vectors for a tenant.

    Args:
        tenant_id: Tenant ID to delete
        qdrant_url: Optional Qdrant URL

    Returns:
        dict with deletion status
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(url=qdrant_url or QDRANT_URL)

    client.delete(
        collection_name=COLLECTION_NAME,
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
