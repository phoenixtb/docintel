# Analytics Service

**Language/Framework:** Python · FastAPI  
**Port:** `8001`  
**Source:** `services/analytics-service-py/`

> Note: There is also a stub Kotlin analytics service at `services/analytics-service/` (Spring Boot + ClickHouse). The active deployed service is the Python implementation (`analytics-service-py`).

---

## Responsibilities

- Ingest query telemetry events from the RAG service (fire-and-forget)
- Ingest user feedback events (like/dislike) from the frontend
- Serve aggregated analytics (feedback summary, query stats) for admin dashboards

---

## Data Store

**ClickHouse** — columnar OLAP database, optimized for analytical queries over large event volumes.

Two tables (auto-created at startup via `ensure_schema()`):

| Table | Columns | Description |
|-------|---------|-------------|
| `query_events` | `query_id`, `tenant_id`, `user_id`, `latency_ms`, `model_used`, `cache_hit`, `source_count` | One row per RAG query |
| `feedback_events` | `query_id`, `tenant_id`, `user_id`, `liked`, `comment` | One row per user feedback submission |

---

## API Endpoints

| Method | Path | Caller | Description |
|--------|------|--------|-------------|
| `POST` | `/events/query` | RAG service (fire-and-forget) | Ingest query telemetry |
| `POST` | `/events/feedback` | Web UI | Ingest like/dislike feedback |
| `GET` | `/analytics/feedback/summary` | Admin dashboard | Aggregate like/dislike counts; optional `?tenant_id=` filter |
| `GET` | `/analytics/queries/summary` | Admin dashboard | Total queries, avg latency, cache hit rate; optional `?tenant_id=` filter |
| `GET` | `/health` | Gateway / monitoring | ClickHouse connectivity check |

### `POST /events/query` body

```json
{
  "query_id": "uuid",
  "tenant_id": "alpha",
  "user_id": "user-sub",
  "latency_ms": 1230,
  "model_used": "qwen3.5:4b",
  "cache_hit": false,
  "source_count": 5
}
```

### `POST /events/feedback` body

```json
{
  "query_id": "uuid",
  "tenant_id": "alpha",
  "user_id": "user-sub",
  "liked": true,
  "comment": "Very helpful!"
}
```

### `GET /analytics/feedback/summary` response

```json
{ "liked": 42, "disliked": 3, "total": 45 }
```

### `GET /analytics/queries/summary` response

```json
{
  "total_queries": 1234,
  "avg_latency_ms": 1850.3,
  "cache_hit_rate": 0.312
}
```

---

## Integration with RAG Service

The RAG service calls `_emit_query_event()` after every query (streaming or non-streaming). This is a fire-and-forget async HTTP call with a 3s timeout — analytics failures never block query responses.

```python
await client.post(f"{analytics_url}/events/query", json={...})
```

---

## Key Files

| File | Purpose |
|------|---------|
| `src/main.py` | FastAPI app, all endpoints, lifespan |
| `src/config.py` | `Settings` (ClickHouse host, port, DB, credentials) |
| `src/db.py` | ClickHouse client factory, `ensure_schema()` |
| `src/models.py` | Pydantic models: `QueryEvent`, `FeedbackEvent` |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CLICKHOUSE_HOST` | `clickhouse` | ClickHouse hostname |
| `CLICKHOUSE_PORT` | `8123` | ClickHouse HTTP port |
| `CLICKHOUSE_DATABASE` | `docintel_analytics` | Database name |
| `CLICKHOUSE_USER` | `default` | ClickHouse user |
| `CLICKHOUSE_PASSWORD` | `` | ClickHouse password |
