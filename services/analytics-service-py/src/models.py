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
    thinking_truncated: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    # B1/B3/B4 — query text (top-queries / corpus-gap detection), retrieval +
    # rerank transparency data, and the Langfuse trace id for deep-linking.
    query_text: str = ""
    retrieval_mode: str = ""
    rerank_candidates_in: int = 0
    rerank_candidates_out: int = 0
    reranker_degraded: bool = False
    trace_id: str = ""


class FeedbackEvent(BaseModel):
    query_id: str
    tenant_id: str
    user_id: str = ""
    liked: Optional[bool] = None
    comment: Optional[str] = None
    # B1 — denormalized at feedback time (frontend already has these in memory
    # when the user clicks thumbs up/down) so the Feedback Review page can
    # show query/answer/sources without joining back to conversation history.
    query_text: str = ""
    answer_text: str = ""
    sources_json: str = ""
