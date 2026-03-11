"""
Semantic Cache Components
=========================

Check and write to semantic response cache in Qdrant.
Uses query embedding similarity to find cached responses.
"""

from haystack import component
from qdrant_client import QdrantClient, models
from typing import Optional
import uuid
from datetime import datetime, timezone
import os


@component
class SemanticCacheChecker:
    """
    Check Qdrant for semantically similar cached responses.
    Returns cached response if similarity > threshold.
    Uses query_points() API (qdrant-client v1.16+).
    """

    def __init__(
        self,
        qdrant_url: str | None = None,
        collection: str = "response_cache",
        threshold: float = 0.92,
    ):
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
        self.client = QdrantClient(url=self.qdrant_url)
        self.collection = collection
        self.threshold = threshold
        self._ensure_collection()

    def _ensure_collection(self):
        """Create collection if it doesn't exist."""
        collections = self.client.get_collections().collections
        if not any(c.name == self.collection for c in collections):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=768,  # nomic-embed-text dimension
                    distance=models.Distance.COSINE,
                ),
            )
            # Create tenant index
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="tenant_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    @component.output_types(
        cache_hit=bool,
        cached_response=Optional[str],
        cached_sources=Optional[list],
        query_embedding=list[float],
    )
    def run(self, query_embedding: list[float], tenant_id: str) -> dict:
        results = self.client.query_points(
            collection_name=self.collection,
            query=query_embedding,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    )
                ]
            ),
            limit=1,
            score_threshold=self.threshold,
        )

        if results.points:
            payload = results.points[0].payload
            return {
                "cache_hit": True,
                "cached_response": payload.get("response"),
                "cached_sources": payload.get("sources", []),
                "query_embedding": query_embedding,
            }

        return {
            "cache_hit": False,
            "cached_response": None,
            "cached_sources": None,
            "query_embedding": query_embedding,
        }


@component
class SemanticCacheWriter:
    """Write successful responses to semantic cache."""

    def __init__(
        self,
        qdrant_url: str | None = None,
        collection: str = "response_cache",
    ):
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
        self.client = QdrantClient(url=self.qdrant_url)
        self.collection = collection

    @component.output_types(cached=bool)
    def run(
        self,
        query: str,
        query_embedding: list[float],
        response: str,
        sources: list[dict],
        tenant_id: str,
    ) -> dict:
        self.client.upsert(
            collection_name=self.collection,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=query_embedding,
                    payload={
                        "query": query,
                        "response": response,
                        "sources": sources,
                        "tenant_id": tenant_id,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            ],
        )
        return {"cached": True}


def invalidate_cache_for_tenant(
    tenant_id: str,
    qdrant_url: str | None = None,
    collection: str = "response_cache",
) -> dict:
    """
    Invalidate (delete) all cached responses for a tenant.
    
    Should be called when documents are added/removed to ensure
    fresh responses are generated with new content.
    
    Args:
        tenant_id: Tenant ID to invalidate cache for
        qdrant_url: Optional Qdrant URL
        collection: Cache collection name
        
    Returns:
        dict with invalidation status
    """
    url = qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=url)
    
    # Check if collection exists
    collections = client.get_collections().collections
    if not any(c.name == collection for c in collections):
        return {"invalidated": False, "reason": "collection_not_exists"}
    
    # Delete all cache entries for this tenant
    client.delete(
        collection_name=collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    )
                ]
            )
        ),
    )
    
    return {"invalidated": True, "tenant_id": tenant_id}
