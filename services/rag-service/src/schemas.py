"""
RAG Response Schemas
====================

Pydantic models for structured LLM output.
"""

from pydantic import BaseModel


class RAGStructuredResponse(BaseModel):
    """Schema for constrained RAG answer output."""

    answer: str  # Main answer text with [1], [2] citation markers
