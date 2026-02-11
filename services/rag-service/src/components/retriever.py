"""
Secure Retriever Component
==========================

Wraps retrieval with access control + domain filtering.
Applies tenant + role + domain filters at the Qdrant query level.
"""

from haystack import component, Document
from qdrant_client import QdrantClient, models
import os


@component
class SecureRetriever:
    """
    Wraps retrieval with access control + domain filtering.
    Applies tenant + role + domain filters at the Qdrant query level.

    Combines:
      - Tenant isolation (required)
      - Role-based ACLs (optional)
      - Domain filtering from router (optional)
    """

    def __init__(
        self,
        qdrant_url: str | None = None,
        collection: str = "documents",
        top_k: int = 50,
    ):
        self.qdrant_url = qdrant_url or os.getenv("QDRANT_URL", "http://localhost:6333")
        self.client = QdrantClient(url=self.qdrant_url)
        self.collection = collection
        self.top_k = top_k

    @component.output_types(documents=list[Document])
    def run(
        self,
        query_embedding: list[float],
        tenant_id: str,
        user_roles: list[str] | None = None,
        user_id: str | None = None,
        domain_filter: dict | None = None,
    ) -> dict:
        # Build ACL filter: tenant_id required
        # Note: Haystack stores metadata nested under 'meta' key
        must_conditions = [
            models.FieldCondition(
                key="meta.tenant_id",  # Haystack nests metadata under 'meta'
                match=models.MatchValue(value=tenant_id),
            )
        ]

        # Add domain filter if provided by router
        if domain_filter:
            must_conditions.append(
                models.FieldCondition(
                    key=f"meta.{domain_filter['key']}",  # Also nested under 'meta'
                    match=models.MatchValue(value=domain_filter["match"]["value"]),
                )
            )

        # Optional role-based filtering within tenant
        # Note: These are also nested under 'meta'
        should_conditions = []
        if user_roles:
            should_conditions.append(
                models.FieldCondition(
                    key="meta.allowed_roles",
                    match=models.MatchAny(any=user_roles),
                )
            )
        if user_id:
            should_conditions.append(
                models.FieldCondition(
                    key="meta.allowed_users",
                    match=models.MatchValue(value=user_id),
                )
            )

        # Build complete filter
        query_filter = models.Filter(must=must_conditions)
        if should_conditions:
            query_filter.should = should_conditions

        # Retrieve with all filters applied at DB level
        results = self.client.query_points(
            collection_name=self.collection,
            query=query_embedding,
            query_filter=query_filter,
            limit=self.top_k,
        )

        # Convert to Haystack Documents
        documents = []
        for point in results.points:
            documents.append(
                Document(
                    id=str(point.id),
                    content=point.payload.get("content", ""),
                    meta=point.payload,
                    score=point.score,
                )
            )

        return {"documents": documents}


@component
class TenantFilter:
    """
    Legacy filter for post-retrieval tenant isolation.
    Prefer SecureRetriever for pre-filter ACLs.
    """

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document], tenant_id: str) -> dict:
        return {
            "documents": [
                doc for doc in documents if doc.meta.get("tenant_id") == tenant_id
            ]
        }
