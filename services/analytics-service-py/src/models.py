from typing import Optional
from pydantic import BaseModel


class QueryEvent(BaseModel):
    query_id: str
    tenant_id: str
    user_id: str = ""
    latency_ms: int = 0
    model_used: str = ""
    cache_hit: bool = False
    source_count: int = 0


class FeedbackEvent(BaseModel):
    query_id: str
    tenant_id: str
    user_id: str = ""
    liked: Optional[bool] = None
    comment: Optional[str] = None
