"""
Semantic Cache Components
=========================

Check and write to semantic response cache in Qdrant.

Cache entries are scoped by:
  - tenant_id          (mandatory)
  - max_classification_level (int) — max classification across retrieved chunks
  - required_roles     (list[str]) — union of allowed_roles from retrieved chunks

SemanticCacheChecker enforces that:
  1. The user's clearance level covers the max classification of the cached response
  2. The user holds at least one of the required_roles (or required_roles is empty)
This prevents cross-user cache leakage of sensitive content.
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from haystack import component
from qdrant_client import QdrantClient, models

from docintel_common.security import CLASSIFICATION_ORDER, Classification, UserContext


@component
class SemanticCacheChecker:
    """
    Check Qdrant for semantically similar cached responses.

    Enforces clearance and role-based access before returning a cache hit.
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
        collections = self.client.get_collections().collections
        if not any(c.name == self.collection for c in collections):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=768,
                    distance=models.Distance.COSINE,
                ),
            )
            for field, schema in [
                ("tenant_id", models.PayloadSchemaType.KEYWORD),
                ("max_classification_level", models.PayloadSchemaType.INTEGER),
            ]:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=schema,
                )

    @component.output_types(
        cache_hit=bool,
        cached_response=Optional[str],
        cached_sources=Optional[list],
        query_embedding=list[float],
    )
    def run(
        self,
        query_embedding: list[float],
        tenant_id: str,
        user_context: Optional[UserContext] = None,
    ) -> dict:
        user_clearance_int = (
            CLASSIFICATION_ORDER.get(user_context.clearance, 0)
            if user_context
            else CLASSIFICATION_ORDER[Classification.INTERNAL]
        )
        user_roles = set(user_context.roles) if user_context else set()

        # Pre-filter in Qdrant: tenant + clearance level
        results = self.client.query_points(
            collection_name=self.collection,
            query=query_embedding,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    ),
                    models.FieldCondition(
                        key="max_classification_level",
                        range=models.Range(lte=user_clearance_int),
                    ),
                ]
            ),
            limit=5,
            score_threshold=self.threshold,
        )

        # Post-filter in Python: role check (Qdrant can't express "empty OR intersects" natively)
        for point in results.points:
            payload = point.payload or {}
            required_roles: list[str] = payload.get("required_roles", [])

            # open doc (no role restriction) OR user holds at least one required role
            if not required_roles or (user_roles & set(required_roles)):
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
    """
    Write successful responses to semantic cache with ACL-scoped metadata.

    Stores max_classification_level (int) and required_roles (list) so
    SemanticCacheChecker can enforce access control on cache hits.
    """

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
        max_classification: str = "internal",
        required_roles: Optional[list[str]] = None,
    ) -> dict:
        max_classification_int = CLASSIFICATION_ORDER.get(
            Classification(max_classification) if max_classification in Classification._value2member_map_ else Classification.INTERNAL,
            CLASSIFICATION_ORDER[Classification.INTERNAL],
        )

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
                        "max_classification": max_classification,
                        "max_classification_level": max_classification_int,
                        "required_roles": required_roles or [],
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

    Called when documents are added/removed to prevent stale cache hits
    that reference deleted content.
    """
    url = qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=url)

    collections = client.get_collections().collections
    if not any(c.name == collection for c in collections):
        return {"invalidated": False, "reason": "collection_not_exists"}

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
