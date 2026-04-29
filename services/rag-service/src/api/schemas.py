"""
API request/response schemas.

Defined here (rather than main.py) so they can be imported by both
main.py and dependencies.py without circular imports.
"""

from typing import Optional

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    tenant_id: str = Field(default="default")
    user_roles: list[str] = Field(default_factory=list)
    user_id: Optional[str] = None
    document_type: Optional[str] = None
    conversation_id: Optional[str] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    use_cache: bool = True
    use_reranking: bool = True
    # None = use tenant preference; true/false = per-query override
    thinking_mode: Optional[bool] = None


class QueryResponse(BaseModel):
    answer: str
    thinking: str = ""
    sources: list[dict]
    cache_hit: bool
    latency_ms: int
    model_used: str
    routed_domain: Optional[str] = None
