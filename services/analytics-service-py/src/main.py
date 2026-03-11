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

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .db import ensure_schema, get_client
from .models import FeedbackEvent, QueryEvent

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
    yield


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


def _settings() -> Settings:
    return get_settings()


# =============================================================================
# Events
# =============================================================================

@app.post("/events/query", status_code=204)
async def ingest_query_event(event: QueryEvent):
    """Called by rag-service (fire-and-forget) after each query."""
    settings = _settings()
    db = settings.clickhouse_database
    try:
        client = get_client(settings)
        client.insert(
            f"{db}.query_events",
            [[
                event.query_id, event.tenant_id, event.user_id,
                event.latency_ms, event.model_used,
                event.cache_hit, event.source_count,
            ]],
            column_names=[
                "query_id", "tenant_id", "user_id",
                "latency_ms", "model_used", "cache_hit", "source_count",
            ],
        )
    except Exception as e:
        logger.warning("Failed to insert query_event: %s", e)
        raise HTTPException(status_code=500, detail="Event ingestion failed")


@app.post("/events/feedback", status_code=204)
async def ingest_feedback_event(event: FeedbackEvent):
    """Called by frontend on like/dislike."""
    settings = _settings()
    db = settings.clickhouse_database
    try:
        client = get_client(settings)
        client.insert(
            f"{db}.feedback_events",
            [[
                event.query_id, event.tenant_id, event.user_id,
                event.liked, event.comment,
            ]],
            column_names=["query_id", "tenant_id", "user_id", "liked", "comment"],
        )
    except Exception as e:
        logger.warning("Failed to insert feedback_event: %s", e)
        raise HTTPException(status_code=500, detail="Event ingestion failed")


# =============================================================================
# Analytics
# =============================================================================

def _resolve_tenant(request: Request, query_tenant_id: str | None) -> str | None:
    """
    Resolve tenant_id from the trusted X-Tenant-Id header (set by the gateway).
    Falls back to the query parameter only if the header is absent (e.g. direct
    internal calls from rag-service / admin-service that set tenant_id in the body).
    Never trusts raw query strings from untrusted clients.
    """
    header_tenant = request.headers.get("X-Tenant-Id")
    if header_tenant and header_tenant not in ("", "default"):
        return header_tenant
    return query_tenant_id


@app.get("/analytics/feedback/summary")
async def feedback_summary(
    request: Request,
    tenant_id: str | None = None,
):
    """Aggregate like/dislike counts. Tenant scoped via X-Tenant-Id header."""
    effective_tenant = _resolve_tenant(request, tenant_id)
    settings = _settings()
    db = settings.clickhouse_database
    try:
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
    try:
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
    except Exception as e:
        logger.error("Analytics query failed: %s", e)
        raise HTTPException(status_code=500, detail="Analytics query failed")


# =============================================================================
# Health
# =============================================================================

@app.get("/health")
async def health():
    settings = _settings()
    try:
        client = get_client(settings)
        client.command("SELECT 1")
        ch_status = "connected"
    except Exception as e:
        ch_status = f"error: {str(e)[:60]}"
    return {
        "status": "healthy" if ch_status == "connected" else "degraded",
        "clickhouse": ch_status,
        "version": settings.service_version,
    }
