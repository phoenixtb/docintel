"""
Analytics Service
=================

Platform telemetry backbone for DocIntel.
Accepts query events from rag-service and feedback events from the frontend.
Stores everything in ClickHouse for analytics and future RLHF data export.

Endpoints:
  POST /events/query     — rag-service fires this after every query
  POST /events/feedback  — frontend fires this on like/dislike
  GET  /analytics/feedback/summary  — aggregate feedback stats per tenant
  GET  /health
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from docintel_common.errors import install_error_handlers

from .config import Settings, get_settings
from .db import ensure_schema, get_client
from .models import FeedbackEvent, QueryEvent
from .stream_consumer import AnalyticsStreamConsumer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    try:
        ensure_schema(settings)
        logger.info("Analytics Service ready (clickhouse=%s)", settings.clickhouse_host)
    except Exception as e:
        logger.error("ClickHouse schema bootstrap failed: %s", e)

    # A7: background consumer for analytics.query (Redis Streams) — batches
    # rag-service's query telemetry into ClickHouse. POST /events/query stays
    # available as a secondary/manual ingestion path (e.g. curl, tests).
    consumer = AnalyticsStreamConsumer(settings)
    app.state.stream_consumer = consumer
    consumer_task = asyncio.create_task(consumer.run())
    app.state.stream_consumer_task = consumer_task

    yield

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="DocIntel Analytics Service",
    version="0.1.0",
    lifespan=lifespan,
)
install_error_handlers(app)

_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

Instrumentator(
    should_group_status_codes=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics")


def _settings() -> Settings:
    return get_settings()


# =============================================================================
# Events
# =============================================================================

@app.post("/events/query", status_code=202)
async def ingest_query_event(event: QueryEvent):
    """Secondary/manual query-event ingestion path (curl, tests, other producers).

    rag-service's production path publishes to the analytics.query Redis
    stream instead (see stream_consumer.py) — batched inserts, survives
    analytics-service downtime. This endpoint does a direct single-row
    insert and stays available for anything that isn't on the stream.

    202 Accepted: fire-and-forget telemetry, not a durable write acknowledgement.
    """
    settings = _settings()
    db = settings.clickhouse_database

    def _insert():
        client = get_client(settings)
        client.insert(
            f"{db}.query_events",
            [[
                event.query_id, event.tenant_id, event.user_id,
                event.latency_ms, event.model_used,
                event.cache_hit, event.source_count,
                event.thinking_truncated,
                event.prompt_tokens, event.completion_tokens, event.cost_usd,
                event.query_text, event.retrieval_mode,
                event.rerank_candidates_in, event.rerank_candidates_out,
                event.reranker_degraded, event.trace_id,
            ]],
            column_names=[
                "query_id", "tenant_id", "user_id",
                "latency_ms", "model_used", "cache_hit", "source_count",
                "thinking_truncated",
                "prompt_tokens", "completion_tokens", "cost_usd",
                "query_text", "retrieval_mode",
                "rerank_candidates_in", "rerank_candidates_out",
                "reranker_degraded", "trace_id",
            ],
        )

    try:
        await asyncio.to_thread(_insert)
    except Exception as e:
        logger.warning("Failed to insert query_event: %s", e)
        raise HTTPException(status_code=500, detail="Event ingestion failed")


@app.post("/events/feedback", status_code=202)
async def ingest_feedback_event(event: FeedbackEvent):
    """Called by frontend on like/dislike."""
    settings = _settings()
    db = settings.clickhouse_database

    def _insert():
        client = get_client(settings)
        client.insert(
            f"{db}.feedback_events",
            [[
                event.query_id, event.tenant_id, event.user_id,
                event.liked, event.comment,
                event.query_text, event.answer_text, event.sources_json,
            ]],
            column_names=[
                "query_id", "tenant_id", "user_id", "liked", "comment",
                "query_text", "answer_text", "sources_json",
            ],
        )

    try:
        await asyncio.to_thread(_insert)
    except Exception as e:
        logger.warning("Failed to insert feedback_event: %s", e)
        raise HTTPException(status_code=500, detail="Event ingestion failed")


# =============================================================================
# Analytics
# =============================================================================

def _resolve_tenant(request: Request, _query_tenant_id: str | None) -> str | None:
    """
    Resolve the effective tenant filter for analytics reads.

    Role-aware (ported from the Kotlin analytics-service): `platform_admin`
    (from the gateway-injected X-User-Role header) sees global aggregates
    (no tenant filter). Every other role is forced to their own tenant from
    the trusted X-Tenant-Id header. The tenant_id query parameter is never
    trusted for scoping — a client could otherwise pass an arbitrary
    tenant_id to view another tenant's stats — so it is intentionally
    unused; a non-admin caller with no tenant header gets an empty
    (non-matching) scope rather than an attacker-controlled one.
    """
    role = request.headers.get("X-User-Role", "tenant_user")
    if role == "platform_admin":
        return None

    header_tenant = request.headers.get("X-Tenant-Id")
    if header_tenant and header_tenant not in ("", "default"):
        return header_tenant
    return None


def _window_to_hours(window: str) -> int:
    """Parse a window string ('24h', '7d', '30d') into a whole number of hours.
    Falls back to 7 days on anything unparseable."""
    w = (window or "7d").strip().lower()
    try:
        if w.endswith("h"):
            return max(1, int(w[:-1]))
        if w.endswith("d"):
            return max(1, int(w[:-1]) * 24)
        return max(1, int(w) * 24)  # bare number = days
    except ValueError:
        return 7 * 24


@app.get("/analytics/feedback/summary")
async def feedback_summary(
    request: Request,
    tenant_id: str | None = None,
):
    """Aggregate like/dislike counts. Tenant scoped via X-Tenant-Id header."""
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database

    def _run():
        client = get_client(settings)
        if effective_tenant:
            result = client.query(
                f"SELECT countIf(liked = true), countIf(liked = false), count()"
                f" FROM {db}.feedback_events WHERE tenant_id = {{tenant_id:String}}",
                parameters={"tenant_id": effective_tenant},
            )
        else:
            result = client.query(
                f"SELECT countIf(liked = true), countIf(liked = false), count()"
                f" FROM {db}.feedback_events"
            )
        row = result.first_row
        return {"liked": row[0], "disliked": row[1], "total": row[2]}

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics query failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


@app.get("/analytics/queries/summary")
async def queries_summary(
    request: Request,
    tenant_id: str | None = None,
):
    """Aggregate query stats: avg latency, cache hit rate, query count."""
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database

    def _run():
        client = get_client(settings)
        if effective_tenant:
            result = client.query(
                f"SELECT count(), avg(latency_ms), countIf(cache_hit) / count()"
                f" FROM {db}.query_events WHERE tenant_id = {{tenant_id:String}}",
                parameters={"tenant_id": effective_tenant},
            )
        else:
            result = client.query(
                f"SELECT count(), avg(latency_ms), countIf(cache_hit) / count()"
                f" FROM {db}.query_events"
            )
        row = result.first_row
        return {
            "total_queries": row[0],
            "avg_latency_ms": round(row[1], 1),
            "cache_hit_rate": round(row[2], 3),
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics query failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


@app.get("/analytics/queries/timeseries")
async def queries_timeseries(
    request: Request,
    tenant_id: str | None = None,
    bucket: str = "day",
    days: int = 30,
):
    """Time-series of query count + avg latency bucketed by hour or day."""
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database
    trunc = "toStartOfHour" if bucket == "hour" else "toStartOfDay"

    def _run():
        client = get_client(settings)
        if effective_tenant:
            result = client.query(
                f"SELECT {trunc}(created_at) AS ts, count() AS cnt,"
                f" avg(latency_ms) AS avg_ms, quantile(0.95)(latency_ms) AS p95_ms,"
                f" countIf(cache_hit) / count() AS hit_rate"
                f" FROM {db}.query_events"
                f" WHERE tenant_id = {{tenant_id:String}}"
                f"   AND created_at >= now() - INTERVAL {{days:UInt32}} DAY"
                f" GROUP BY ts ORDER BY ts",
                parameters={"tenant_id": effective_tenant, "days": days},
            )
        else:
            result = client.query(
                f"SELECT {trunc}(created_at) AS ts, count() AS cnt,"
                f" avg(latency_ms) AS avg_ms, quantile(0.95)(latency_ms) AS p95_ms,"
                f" countIf(cache_hit) / count() AS hit_rate"
                f" FROM {db}.query_events"
                f" WHERE created_at >= now() - INTERVAL {{days:UInt32}} DAY"
                f" GROUP BY ts ORDER BY ts",
                parameters={"days": days},
            )
        return [
            {
                "ts": str(row[0]),
                "count": row[1],
                "avg_latency_ms": round(row[2], 1),
                "p95_latency_ms": round(row[3], 1),
                "cache_hit_rate": round(row[4], 3),
            }
            for row in result.result_rows
        ]

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics timeseries failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


@app.get("/analytics/queries/by-model")
async def queries_by_model(
    request: Request,
    tenant_id: str | None = None,
    days: int = 30,
):
    """Query count and avg latency grouped by model_used."""
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database

    def _run():
        client = get_client(settings)
        if effective_tenant:
            result = client.query(
                f"SELECT model_used, count() AS cnt, avg(latency_ms) AS avg_ms"
                f" FROM {db}.query_events"
                f" WHERE tenant_id = {{tenant_id:String}}"
                f"   AND created_at >= now() - INTERVAL {{days:UInt32}} DAY"
                f" GROUP BY model_used ORDER BY cnt DESC",
                parameters={"tenant_id": effective_tenant, "days": days},
            )
        else:
            result = client.query(
                f"SELECT model_used, count() AS cnt, avg(latency_ms) AS avg_ms"
                f" FROM {db}.query_events"
                f" WHERE created_at >= now() - INTERVAL {{days:UInt32}} DAY"
                f" GROUP BY model_used ORDER BY cnt DESC",
                parameters={"days": days},
            )
        return [
            {"model": row[0], "count": row[1], "avg_latency_ms": round(row[2], 1)}
            for row in result.result_rows
        ]

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics by-model failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


@app.get("/analytics/feedback/timeseries")
async def feedback_timeseries(
    request: Request,
    tenant_id: str | None = None,
    bucket: str = "day",
    days: int = 30,
):
    """Time-series of likes/dislikes bucketed by day."""
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database
    trunc = "toStartOfHour" if bucket == "hour" else "toStartOfDay"

    def _run():
        client = get_client(settings)
        if effective_tenant:
            result = client.query(
                f"SELECT {trunc}(created_at) AS ts,"
                f" countIf(liked = true) AS likes, countIf(liked = false) AS dislikes"
                f" FROM {db}.feedback_events"
                f" WHERE tenant_id = {{tenant_id:String}}"
                f"   AND created_at >= now() - INTERVAL {{days:UInt32}} DAY"
                f" GROUP BY ts ORDER BY ts",
                parameters={"tenant_id": effective_tenant, "days": days},
            )
        else:
            result = client.query(
                f"SELECT {trunc}(created_at) AS ts,"
                f" countIf(liked = true) AS likes, countIf(liked = false) AS dislikes"
                f" FROM {db}.feedback_events"
                f" WHERE created_at >= now() - INTERVAL {{days:UInt32}} DAY"
                f" GROUP BY ts ORDER BY ts",
                parameters={"days": days},
            )
        return [
            {"ts": str(row[0]), "likes": row[1], "dislikes": row[2]}
            for row in result.result_rows
        ]

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics feedback timeseries failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


# =============================================================================
# B1 — Insights aggregate endpoints (tenant-scoped, backing web-ui /insights)
# =============================================================================

@app.get("/analytics/usage")
async def analytics_usage(
    request: Request,
    window: str = "7d",
    tenant_id: str | None = None,
):
    """Query volume, unique users, p50/p95 latency, cache-hit ratio for the window."""
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database
    hours = _window_to_hours(window)

    def _run():
        client = get_client(settings)
        params: dict = {"hours": hours}
        where = "WHERE created_at >= now() - INTERVAL {hours:UInt32} HOUR"
        if effective_tenant:
            where += " AND tenant_id = {tenant_id:String}"
            params["tenant_id"] = effective_tenant
        result = client.query(
            f"SELECT count() AS total, uniqExact(user_id) AS unique_users,"
            f" quantile(0.5)(latency_ms) AS p50, quantile(0.95)(latency_ms) AS p95,"
            f" countIf(cache_hit) / count() AS cache_hit_rate"
            f" FROM {db}.query_events {where}",
            parameters=params,
        )
        row = result.first_row
        total = row[0] or 0
        return {
            "window": window,
            "total_queries": total,
            "unique_users": row[1] or 0,
            "p50_latency_ms": round(row[2] or 0.0, 1),
            "p95_latency_ms": round(row[3] or 0.0, 1),
            "cache_hit_rate": round(row[4] or 0.0, 3) if total else 0.0,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics usage query failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


@app.get("/analytics/quality")
async def analytics_quality(
    request: Request,
    window: str = "7d",
    tenant_id: str | None = None,
):
    """
    Abstention rate (queries with 0 sources — the RAG pipeline's no-docs
    branch), reranker-degraded count, feedback score distribution, plus a
    daily breakdown for the Insights quality-over-time chart.
    """
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database
    hours = _window_to_hours(window)

    def _run():
        client = get_client(settings)
        params: dict = {"hours": hours}
        q_where = "WHERE created_at >= now() - INTERVAL {hours:UInt32} HOUR"
        f_where = "WHERE created_at >= now() - INTERVAL {hours:UInt32} HOUR"
        if effective_tenant:
            q_where += " AND tenant_id = {tenant_id:String}"
            f_where += " AND tenant_id = {tenant_id:String}"
            params["tenant_id"] = effective_tenant

        summary = client.query(
            f"SELECT count() AS total,"
            f" countIf(source_count = 0) AS abstained,"
            f" countIf(reranker_degraded) AS reranker_degraded_count"
            f" FROM {db}.query_events {q_where}",
            parameters=params,
        ).first_row

        feedback = client.query(
            f"SELECT countIf(liked = true) AS likes, countIf(liked = false) AS dislikes, count() AS total"
            f" FROM {db}.feedback_events {f_where}",
            parameters=params,
        ).first_row

        timeseries = client.query(
            f"SELECT toStartOfDay(created_at) AS ts, count() AS total,"
            f" countIf(source_count = 0) AS abstained,"
            f" countIf(reranker_degraded) AS reranker_degraded_count"
            f" FROM {db}.query_events {q_where}"
            f" GROUP BY ts ORDER BY ts",
            parameters=params,
        ).result_rows

        total = summary[0] or 0
        fb_total = feedback[2] or 0
        return {
            "window": window,
            "total_queries": total,
            "abstention_rate": round((summary[1] or 0) / total, 3) if total else 0.0,
            "reranker_degraded_count": summary[2] or 0,
            "feedback": {
                "likes": feedback[0] or 0,
                "dislikes": feedback[1] or 0,
                "total": fb_total,
                "like_rate": round((feedback[0] or 0) / fb_total, 3) if fb_total else 0.0,
            },
            "timeseries": [
                {
                    "ts": str(r[0]),
                    "total": r[1],
                    "abstention_rate": round(r[2] / r[1], 3) if r[1] else 0.0,
                    "reranker_degraded_count": r[3],
                }
                for r in timeseries
            ],
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics quality query failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


@app.get("/analytics/top-queries")
async def analytics_top_queries(
    request: Request,
    window: str = "7d",
    tenant_id: str | None = None,
    limit: int = 20,
):
    """Most frequent queries and zero-result queries (corpus-gap detector)."""
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database
    hours = _window_to_hours(window)
    limit = max(1, min(limit, 100))

    def _run():
        client = get_client(settings)
        params: dict = {"hours": hours, "limit": limit}
        where = (
            "WHERE created_at >= now() - INTERVAL {hours:UInt32} HOUR"
            " AND query_text != ''"
        )
        if effective_tenant:
            where += " AND tenant_id = {tenant_id:String}"
            params["tenant_id"] = effective_tenant

        frequent = client.query(
            f"SELECT query_text, count() AS cnt"
            f" FROM {db}.query_events {where}"
            f" GROUP BY query_text ORDER BY cnt DESC LIMIT {{limit:UInt32}}",
            parameters=params,
        ).result_rows

        zero_result = client.query(
            f"SELECT query_text, count() AS cnt"
            f" FROM {db}.query_events {where} AND source_count = 0"
            f" GROUP BY query_text ORDER BY cnt DESC LIMIT {{limit:UInt32}}",
            parameters=params,
        ).result_rows

        return {
            "window": window,
            "frequent_queries": [{"query_text": r[0], "count": r[1]} for r in frequent],
            "zero_result_queries": [{"query_text": r[0], "count": r[1]} for r in zero_result],
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics top-queries query failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


@app.get("/analytics/feedback")
async def analytics_feedback_list(
    request: Request,
    page: int = 0,
    page_size: int = 20,
    liked: bool | None = None,
    tenant_id: str | None = None,
):
    """
    Paginated feedback items for the Feedback Review page — query + answer
    (+ any sources captured at feedback time) with an ANY LEFT JOIN back to
    query_events for the Langfuse trace_id (B4 deep-link). Defaults to no
    liked filter; the UI defaults its own view to dislikes-only for triage.
    """
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database
    page = max(0, page)
    page_size = max(1, min(page_size, 100))

    def _run():
        client = get_client(settings)
        params: dict = {"limit": page_size, "offset": page * page_size}
        where_clauses = []
        if effective_tenant:
            where_clauses.append("f.tenant_id = {tenant_id:String}")
            params["tenant_id"] = effective_tenant
        if liked is not None:
            where_clauses.append("f.liked = {liked:Bool}")
            params["liked"] = liked
        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        total = client.query(
            f"SELECT count() FROM {db}.feedback_events f {where}",
            parameters=params,
        ).first_row[0]

        rows = client.query(
            f"SELECT f.query_id, f.tenant_id, f.user_id, f.liked, f.comment,"
            f" f.query_text, f.answer_text, f.sources_json, f.created_at, q.trace_id"
            f" FROM {db}.feedback_events f"
            f" ANY LEFT JOIN {db}.query_events q ON f.query_id = q.query_id"
            f" {where}"
            f" ORDER BY f.created_at DESC"
            f" LIMIT {{limit:UInt32}} OFFSET {{offset:UInt32}}",
            parameters=params,
        ).result_rows

        items = []
        for r in rows:
            trace_id = r[9] or ""
            items.append({
                "query_id": r[0],
                "tenant_id": r[1],
                "user_id": r[2],
                "liked": r[3],
                "comment": r[4],
                "query_text": r[5],
                "answer_text": r[6],
                "sources_json": r[7],
                "created_at": str(r[8]),
                "trace_id": trace_id,
                "trace_url": f"{settings.langfuse_public_host}/trace/{trace_id}" if trace_id else None,
            })

        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items,
        }

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        logger.error("Analytics feedback list query failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


# =============================================================================
# Health
# =============================================================================

@app.get("/health")
async def health(request: Request):
    settings = _settings()

    def _ping():
        client = get_client(settings)
        client.command("SELECT 1")

    try:
        await asyncio.to_thread(_ping)
        ch_status = "connected"
    except Exception as e:
        ch_status = f"error: {str(e)[:60]}"

    consumer_task = getattr(request.app.state, "stream_consumer_task", None)
    consumer = getattr(request.app.state, "stream_consumer", None)
    if consumer_task is not None:
        consumer_status = "running" if not consumer_task.done() else "stopped"
    else:
        consumer_status = "not_started"

    return {
        "status": "healthy" if ch_status == "connected" else "degraded",
        "clickhouse": ch_status,
        "version": settings.service_version,
        "analytics_stream_consumer": {
            "status": consumer_status,
            "batches_flushed": consumer.batches_flushed if consumer else 0,
            "events_flushed": consumer.events_flushed if consumer else 0,
        },
    }
