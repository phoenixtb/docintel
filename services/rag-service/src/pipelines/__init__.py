"""
Haystack Pipelines for DocIntel RAG Service.
"""

from .query import RAGService, build_query_pipeline

__all__ = [
    "RAGService",
    "build_query_pipeline",
]
