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
            ]],
            column_names=[
                "query_id", "tenant_id", "user_id",
                "latency_ms", "model_used", "cache_hit", "source_count",
                "thinking_truncated",
                "prompt_tokens", "completion_tokens", "cost_usd",
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
            ]],
            column_names=["query_id", "tenant_id", "user_id", "liked", "comment"],
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
