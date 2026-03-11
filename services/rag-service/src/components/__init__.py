"""
Custom Haystack Components for DocIntel RAG Service.
All components follow Haystack 2.x @component API.
"""

from .cache import SemanticCacheChecker, SemanticCacheWriter
from .embedders import BM25SparseDocumentEmbedder, BM25SparseTextEmbedder
from .generation import LiteLLMStreamingGenerator
from .model_resolver import TenantModelResolver
from .observability import CostTracker
from .prompt import PromptBuilder
from .query_transform import QueryExpander
from .retrieval import SecureRetriever
from .routing import DomainFilterBuilder

__all__ = [
    # Cache
    "SemanticCacheChecker",
    "SemanticCacheWriter",
    # Embedders
    "BM25SparseDocumentEmbedder",
    "BM25SparseTextEmbedder",
    # Generation
    "LiteLLMStreamingGenerator",
    # Model resolution
    "TenantModelResolver",
    # Observability
    "CostTracker",
    # Prompt
    "PromptBuilder",
    # Query transformation
    "QueryExpander",
    # Retrieval
    "SecureRetriever",
    # Routing
    "DomainFilterBuilder",
]
