"""
ClickHouse client and schema bootstrap.

Two tables owned by this service:
  query_events    — one row per RAG query (latency, model, cache hit)
  feedback_events — one row per user like/dislike with optional comment
"""

import logging

import clickhouse_connect

from .config import Settings

logger = logging.getLogger(__name__)

_CREATE_DATABASE = "CREATE DATABASE IF NOT EXISTS {db}"

_CREATE_QUERY_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.query_events (
    query_id          String,
    tenant_id         String,
    user_id           String,
    latency_ms        UInt32,
    model_used        String,
    cache_hit         Bool,
    source_count      UInt8,
    thinking_truncated Bool DEFAULT false,
    prompt_tokens      UInt32 DEFAULT 0,
    completion_tokens  UInt32 DEFAULT 0,
    cost_usd           Float64 DEFAULT 0.0,
    query_text          String DEFAULT '',
    retrieval_mode       String DEFAULT '',
    rerank_candidates_in  UInt16 DEFAULT 0,
    rerank_candidates_out UInt16 DEFAULT 0,
    reranker_degraded     Bool DEFAULT false,
    trace_id              String DEFAULT '',
    created_at        DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (tenant_id, created_at)
"""

_ALTER_QUERY_EVENTS_THINKING_TRUNCATED = """
ALTER TABLE {db}.query_events
    ADD COLUMN IF NOT EXISTS thinking_truncated Bool DEFAULT false
"""

_ALTER_QUERY_EVENTS_TOKENS_COST = """
ALTER TABLE {db}.query_events
    ADD COLUMN IF NOT EXISTS prompt_tokens UInt32 DEFAULT 0,
    ADD COLUMN IF NOT EXISTS completion_tokens UInt32 DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cost_usd Float64 DEFAULT 0.0
"""

# B1/B3/B4 — Insights transparency + corpus-gap + trace deep-link columns.
_ALTER_QUERY_EVENTS_INSIGHTS = """
ALTER TABLE {db}.query_events
    ADD COLUMN IF NOT EXISTS query_text String DEFAULT '',
    ADD COLUMN IF NOT EXISTS retrieval_mode String DEFAULT '',
    ADD COLUMN IF NOT EXISTS rerank_candidates_in UInt16 DEFAULT 0,
    ADD COLUMN IF NOT EXISTS rerank_candidates_out UInt16 DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reranker_degraded Bool DEFAULT false,
    ADD COLUMN IF NOT EXISTS trace_id String DEFAULT ''
"""

_CREATE_FEEDBACK_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.feedback_events (
    query_id   String,
    tenant_id  String,
    user_id    String,
    liked      Nullable(Bool),
    comment    Nullable(String),
    query_text   String DEFAULT '',
    answer_text  String DEFAULT '',
    sources_json String DEFAULT '',
    created_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (tenant_id, created_at)
"""

# B1 — Feedback Review page needs the query/answer/sources shown at the time
# feedback was given (denormalized from the frontend, not joined at read time).
_ALTER_FEEDBACK_EVENTS_REVIEW_FIELDS = """
ALTER TABLE {db}.feedback_events
    ADD COLUMN IF NOT EXISTS query_text String DEFAULT '',
    ADD COLUMN IF NOT EXISTS answer_text String DEFAULT '',
    ADD COLUMN IF NOT EXISTS sources_json String DEFAULT ''
"""


def get_client(settings: Settings):
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )


def ensure_schema(settings: Settings) -> None:
    db = settings.clickhouse_database
    client = get_client(settings)
    client.command(_CREATE_DATABASE.format(db=db))
    client.command(_CREATE_QUERY_EVENTS.format(db=db))
    client.command(_CREATE_FEEDBACK_EVENTS.format(db=db))
    # Idempotent migrations: add columns to existing tables
    client.command(_ALTER_QUERY_EVENTS_THINKING_TRUNCATED.format(db=db))
    client.command(_ALTER_QUERY_EVENTS_TOKENS_COST.format(db=db))
    client.command(_ALTER_QUERY_EVENTS_INSIGHTS.format(db=db))
    client.command(_ALTER_FEEDBACK_EVENTS_REVIEW_FIELDS.format(db=db))
    logger.info("ClickHouse schema ready (database=%s)", db)
