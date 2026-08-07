# Plan 04 — Hygiene Fixes + User-Facing Observability

Status: IMPLEMENTED (Aug 7, 2026) — Part A in commits A1–A7, Part B in B1–B3
(B4 folded into B3). Open for a live-stack pass: A2 double-harness run,
A3 Grafana visibility.
Executor: Sonnet 5 subagent(s), one phase per agent, reviewed before merge.
Branch: `functional-optimization` (or split per phase).

---

## Part A — Hygiene fixes

Ordered by risk-reduction value. Each item is self-contained.

### A1. Unit tests must not require a live stack
- **Problem:** 6 rag-service tests fail without a running Qdrant (`tests/test_api.py::TestHealthEndpoint::test_health_check`, `tests/test_components.py::TestSecureRetriever*`). CI green depends on docker state.
- **Fix:** mark them `@pytest.mark.integration` and skip when `QDRANT_URL` is unreachable (or mock the Qdrant client). Unit suite must pass on a clean checkout with no containers.
- **Acceptance:** `uv run pytest tests/ -q` passes with all containers stopped; integration-marked tests still run in the compose-based CI job.

### A2. Harness seed/teardown correctness
- **Problem 1:** teardown deletes documents/chunks but leaves `documents.data_sources` rows; next seed dedupes and silently publishes nothing → empty Qdrant, all queries return 0 sources (hit this live in June).
- **Problem 2:** teardown misreports — gateway DELETE returns non-2xx while async deletion actually succeeds, harness prints "Deleted 0 docs, 15 errors".
- **Fix:** (1) teardown must also clear e2e-tenant `data_sources` (via document-service API if one exists, else document the psql fallback and add a `--purge` flag calling it); (2) treat the actual async-deletion status (202/queued) as success in `teardown_e2e_environment` (`tests/integration/run_tests.py:337`).
- **Acceptance:** two consecutive `run_tests.py` invocations both end with non-zero Qdrant points during query phase and a clean teardown report.

### A3. Prometheus scrape gaps
- **Problem:** ingestion-service and data-loader expose `/metrics` (Instrumentator) but are absent from `config/prometheus/prometheus.yml`.
- **Fix:** add both scrape jobs, matching the existing job naming style.
- **Acceptance:** Prometheus targets page shows both UP; ingestion chunk-throughput visible in Grafana explore.

### A4. Dead code removal
- **Problem:** Kotlin `services/analytics-service` duplicates `analytics-service-py` (compose builds only the Python one). `services/ingestion-service/src/db.py` is a deprecated stub. `run_tests.py` still references deleted `requirements.txt` in an error message.
- **Fix:** delete the Kotlin analytics service (git history preserves it), delete the stub, fix the error string to reference `uv sync`. **Before deletion, port two behaviors from the Kotlin impl into `analytics-service-py`:** (1) role-aware tenant scoping — `platform_admin` (from `X-User-Role`) gets global aggregates, everyone else is forced to their own tenant (Python currently has no role awareness); (2) return `202 Accepted` on event ingestion instead of 204 (fire-and-forget semantics).
- **Also:** analytics-py handlers are `async def` but call the sync `clickhouse-connect` client — blocks the event loop. Offload via `run_in_executor`/`asyncio.to_thread` or make handlers sync (uvicorn threadpool). (Query-event inserts move to the batched stream consumer in A7; this fix still applies to feedback + read endpoints.)
- **Acceptance:** repo grep for `analytics-service` (Kotlin path) only appears in git history; `rg requirements.txt tests/` empty; platform_admin sees global stats, tenant admin only their own; event ingestion does not block the loop under concurrent load.

### A5. CostTracker — wire or remove
- **Problem:** `services/rag-service/src/components/observability.py` CostTracker exists but is never called.
- **Fix (preferred):** wire into the generation path — record prompt/completion tokens per query, label by tenant + model, expose as Prometheus counters (`rag_llm_tokens_total{kind=prompt|completion}`), and include token counts in the analytics event already posted to analytics-service.
- **Acceptance:** counter increments visible in `/metrics` after a query; token fields present in ClickHouse `query_events`.

### A6. REST `/ingest` debug path
- **Problem:** `INGESTION_REST_ENABLED=false` in prod, endpoint semi-documented.
- **Fix:** keep disabled; add one line to `docs/services/` docs stating it is a debug-only path and the stream is the production route. No code change.

### A7. Analytics ingest via Redis Streams (fire-and-forget + batching)
- **Problem:** rag-service ships query telemetry via HTTP POST per query (`services/rag-service/src/api/main.py` `_post_query_event`); analytics-py inserts row-per-request into ClickHouse — the known anti-pattern (part explosion). Events are lost whenever analytics-service is down.
- **Design:** reuse the existing bus — all machinery is already in `docintel_common.messaging.RedisStreamBus` (publish, consumer groups, ack, `claim_idle`), and ingestion-service already runs this exact consumer pattern (`stream_worker.py`).
  1. **Producer:** rag-service publishes `analytics.query` to the stream instead of HTTP POST (it already depends on docintel-common; publish with `maxlen` trim ~100k to bound memory).
  2. **Consumer:** analytics-py adds a background consumer task (group `analytics-service`): buffer events, flush to ClickHouse as one batched `client.insert` every N events (e.g. 200) or T seconds (e.g. 5s), whichever first; ack after successful insert.
  3. **Feedback stays REST** (browser → gateway → analytics) with direct insert — human-click volume, batching pointless.
  4. **Semantics:** at-least-once — duplicates possible on crash-between-insert-and-ack. Acceptable for analytics; optionally use `ReplacingMergeTree(query_id)` for exactness. Use `claim_idle` for orphaned messages (same as ingestion).
  5. **Ops:** Prometheus gauge for consumer lag (XINFO GROUPS lag) + counter for batch flushes; alert path via existing Prometheus.
- **Benefits:** query path sheds the HTTP call; events survive analytics downtime (stream retention); ClickHouse gets proper batches.
- **Acceptance:** harness run with analytics-service stopped mid-run loses zero events after it restarts (consumer catches up); ClickHouse receives batched inserts (verify part count stays flat under load); feedback endpoint unchanged.

---

## Part B — User-facing observability

### Current state (honest)
- **Plumbing is strong:** Prometheus + Grafana (5 dashboards), Langfuse traces, ClickHouse `query_events` + `feedback`, reranker-degraded signal, cache metrics.
- **User-visible is thin:** end users see sources + relevance badges; tenant admins see almost nothing (no usage, no quality, no feedback review); operators must leave the product for Grafana/Langfuse.

### Decision: extend the existing web-ui — do NOT build a second UI project
Rationale: web-ui already has OIDC, gateway wiring, tenant context, and role-aware nav. A separate dashboard app duplicates auth/session/build for zero gain. Grafana stays the *operator* surface; the product gets an *Insights* section for tenant-facing analytics. Revisit a separate app only if Insights outgrows SvelteKit (unlikely).

### B1. Analytics API hardening (backend first)
- Extend `analytics-service-py` with tenant-scoped aggregate endpoints (all backed by existing ClickHouse tables):
  - `GET /analytics/usage?window=7d` — query volume, unique users, p50/p95 latency, cache-hit ratio
  - `GET /analytics/quality?window=7d` — abstention rate, reranker-degraded count, feedback score distribution
  - `GET /analytics/feedback?page=` — feedback items with query text + answer for review
  - `GET /analytics/top-queries?window=7d` — frequent queries, zero-result queries (corpus-gap detector)
- Gateway routes under `/api/v1/analytics/*` with tenant-admin RBAC (OPA policy addition).
- **Acceptance:** endpoints return correct aggregates for the e2e tenant after a harness run.

### B2. Insights section in web-ui (tenant admin)
Pages (SvelteKit routes under `/insights`, visible to tenant-admin role):
1. **Usage** — query volume timeline, latency percentiles, cache-hit ratio, active users.
2. **Quality** — abstention rate over time, reranker health, feedback scores; zero-result queries list ("what are users asking that we can't answer" — direct corpus-improvement signal).
3. **Feedback review** — thumbs-down browser with query/answer/sources for triage.
- Charts: use a lightweight chart lib already compatible with Svelte 5 (e.g. layerchart or chart.js) — pick during implementation, no new UI framework.
- **Acceptance:** tenant admin sees all three pages with live data; non-admin gets 403/nav-hidden.

### B3. Per-answer transparency (end user)
- Add an expandable "Why this answer" panel on each assistant message: retrieval mode (hybrid/dense), domain routed to, number of candidates in/out of rerank, cache hit or not, reranker degraded flag (already shown), and per-source scores (already shown).
- Data source: metadata the SSE stream already carries (`MetadataEvent` + sources); small additions if fields missing.
- **Acceptance:** panel renders from stream metadata with no extra API call.

### B4. Operator deep-link glue
- From Insights quality page, link each query row to its Langfuse trace (trace id is already generated in rag-service `tracing.py` — propagate it into `query_events`).
- **Acceptance:** click-through from a feedback item to the full Langfuse trace works.

### Sequencing
```
A1 → A2 → A3/A4 (parallelizable) → A5 → A7 (after A4's analytics-py cleanup)
B1 → B2 → B3 → B4 (B independent of A except A5 enriches B1 data)
```

### Out of scope (tracked elsewhere)
- ragas adoption, CI quality gates, query expansion → `tasks/05-2026-gaps-plan.md`
- GraphRAG / agentic RAG → `docs/future_features/graphrag-agentic-rag.md`
