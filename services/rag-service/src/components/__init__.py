"""
Custom Haystack Components for DocIntel RAG Service
====================================================

All components follow Haystack 2.x component API.
"""

from .llm import LiteLLMGenerator, LiteLLMStreamingGenerator
from .cache import SemanticCacheChecker, SemanticCacheWriter
from .retriever import SecureRetriever, TenantFilter
from .router import DomainFilterBuilder, QueryExpander, CostTracker
from .prompt import PromptBuilder, SystemPromptBuilder
from src.prompts import DOMAIN_LABELS, RAG_PROMPT_TEMPLATE

__all__ = [
    # LLM
    "LiteLLMGenerator",
    "LiteLLMStreamingGenerator",
    # Cache
    "SemanticCacheChecker",
    "SemanticCacheWriter",
    # Retrieval
    "SecureRetriever",
    "TenantFilter",
    # Router
    "DomainFilterBuilder",
    "QueryExpander",
    "CostTracker",
    "DOMAIN_LABELS",
    # Prompt
    "PromptBuilder",
    "SystemPromptBuilder",
    "RAG_PROMPT_TEMPLATE",
]
