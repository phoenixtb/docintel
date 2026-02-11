"""
Haystack Pipelines for DocIntel RAG Service
============================================

Indexing and query pipelines for document processing.
"""

from .indexing import (
    create_document_store,
    create_indexing_pipeline,
    index_chunks,
    delete_document_vectors,
    delete_tenant_vectors,
)
from .query import RAGQueryPipeline, get_query_pipeline

__all__ = [
    # Indexing
    "create_document_store",
    "create_indexing_pipeline",
    "index_chunks",
    "delete_document_vectors",
    "delete_tenant_vectors",
    # Query
    "RAGQueryPipeline",
    "get_query_pipeline",
]
