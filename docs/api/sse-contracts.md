# SSE Client Contracts

DocIntel has four Server-Sent Events (SSE) endpoints. All four require the
`Authorization: Bearer <token>` header, which the browser's native `EventSource`
cannot set — the web-ui uses `fetch()` with a `ReadableStream` reader instead
of `EventSource`, and any other client (Flutter, curl, mobile) must do the same.

```
Accept: text/event-stream
Authorization: Bearer <access_token>
```

There is no cookie-based fallback for any of these endpoints.

---

## 1. Query answer stream

`POST /api/v1/query/stream` (rag-service)

Request body is the same JSON as `POST /api/v1/query` (see the OpenAPI spec).
The response is `text/event-stream`. Every event is an **unnamed** SSE message
(no `event:` field — default type `"message"`) with a single `data:` line of
JSON. There is no `id:` and no `retry:` — reconnection is a fresh `POST`
(see **Reconnection** below), not `EventSource`-style resume.

### Event payloads (in typical order)

| Payload shape | When |
|---|---|
| `{"metadata": {"query_id", "cache_hit", ...}}` | First event. `cache_hit=false` always sent first; a second `metadata` event with `cache_hit=true` follows immediately if the answer is served from cache. Optional keys added incrementally as pipeline stages complete: `context_state`, `reranker_degraded`, `retrieval_mode`, `rerank_candidates_in`, `rerank_candidates_out`, `query_expanded`, `rerank_skipped`. |
| `{"routing": {"domain", "explicit"}}` | After domain routing resolves. |
| `{"queued": true, "message": "..."}` | Only emitted if the LLM concurrency semaphore is saturated. |
| `{"thinking_token": "..."}` | Only when the resolved model has thinking mode enabled; one per reasoning token. |
| `{"status": "generating_answer"}` | Only when the model re-prefills after its thinking budget is exhausted — the UI should show an indicator since no tokens arrive for several seconds. |
| `{"token": "..."}` | One per answer token — concatenate in order to build the answer text. |
| `{"sources": [...], "done": true}` | Last event in a successful stream. `sources` is the list of cited chunks (id, document, snippet, score, etc. — see OpenAPI spec). |
| `{"error": "..."}` | Terminal error event — no further events follow. |
| `: keepalive` (comment, no `data:`) | Heartbeat — see below. Not a JSON event; discard. |

### Keepalive

A `": keepalive\n\n"` comment line is sent every **~20s** of inactivity between
real events (retrieval, rerank, and first-token latency can each take several
seconds). This is a raw SSE comment — no `data:` field — so any spec-compliant
SSE/fetch-stream parser ignores it automatically as long as it only acts on
lines starting with `data:`. Do not treat a keepalive as an error or as the
end of the stream.

### Reconnection

There is no server-side event replay and no `Last-Event-ID` support. If the
connection drops mid-stream, the client's only option is to re-issue the
`POST /api/v1/query/stream` request from scratch (the same question will
either hit the response cache or regenerate). There is no partial-resume.

### Gateway

Route `rag-service-query-stream`, `response-timeout: -1` (no proxy timeout —
required for slow generations).

---

## 2. Document lifecycle events

`GET /api/v1/documents/events` (document-service)

Named SSE events, each with `id:`, `event:`, `retry: 3000`, and a JSON `data:`
line.

### Events

| `event:` name | `data:` shape | When |
|---|---|---|
| `current_state` | `{"documentId", "status", "stage", "filename", "chunkCount", "errorMessage"}` | Sent once per connect/reconnect: a snapshot of every in-flight (`PENDING`/`PROCESSING`) document for the tenant, so the client can reconcile state without a separate REST call. |
| `document_status` | Same shape as above, plus `progress: {"currentPage", "totalPages", "currentStage"}` when available. | Sent on every status transition (`PENDING` → `PROCESSING` → `COMPLETED`/`FAILED`) for any document belonging to the connected tenant. |

`stage` is a human-readable label derived from `status`: `Queued`,
`Processing`, `Indexed`, `Failed`.

### Keepalive

A comment-only `": keepalive"` line (via `SseEmitter.event().comment(...)`,
which serializes as `: keepalive\n\n`) is sent to every open connection every
**~20s**. Document processing can go minutes between status transitions
(large PDFs, queued jobs) — without this, idle mobile/corporate proxies will
silently drop the connection.

### Reconnection

Each named event carries an `id:` (monotonically increasing) and `retry: 3000`,
so the browser's native `EventSource` reconnect logic (`Last-Event-ID` header,
3s backoff) *would* work — but there is **no server-side replay**: the server
does not buffer or resend events for a given `Last-Event-ID`. In practice this
is fine because reconnecting triggers a fresh `current_state` snapshot event,
which covers the gap. Clients using `fetch()` instead of `EventSource` (required
here, since `EventSource` cannot set `Authorization`) must implement their own
reconnect loop and should treat the `current_state` event on each new
connection as the reconciliation point, not try to resume by event id.

### Gateway

Route `document-service-events`, `response-timeout: -1`.

---

## 3. Cleanup job progress

`GET /api/v1/documents/cleanup/jobs/{jobId}/events` (document-service)

Named SSE events, `id:`, `event:`, `retry: 3000`, JSON `data:`.

### Events

| `event:` name | `data:` shape | When |
|---|---|---|
| `cleanup_progress` | `{"jobId", "status", "total", "processed", "succeeded", "failed"}` | Emitted as the bulk-delete job processes matches. |
| `cleanup_complete` | Same as above, plus `"errors": [...]`, `"completedAt"`. | Terminal event; the server closes the connection immediately after (`emitter.complete()`). |

If the client connects **after** the job has already finished, the server
immediately replays a single `cleanup_complete` event with the final state,
then closes — there is no `current_state`-style snapshot mid-job.

### Keepalive

**None currently.** Cleanup jobs are expected to be short-lived (bounded by
the document count matched by the filter), so no heartbeat was added in this
pass. If cleanup jobs on large tenants start running long enough for idle
proxies to drop the connection, add the same `@Scheduled` comment-heartbeat
pattern used in `SseEmitterRegistry`.

### Reconnection

No replay beyond the late-connect terminal-state case above. A client that
disconnects mid-job and reconnects will only see progress from the point of
reconnect onward (or the terminal event, if the job already finished);
polling `GET /api/v1/documents/cleanup/jobs/{jobId}` (non-SSE) is the fallback
for clients that need the current count without a live stream.

### Gateway

Route `document-service-cleanup-jobs-events`, `response-timeout: -1`.

---

## 4. Dataset load progress

`GET /api/v1/datasets/load/{jobId}/progress` (data-loader)

Unnamed SSE events (`event: <type>` field is set, but see note below — no `id:`,
no `retry:`), JSON `data:`.

### Events

| `event:` name | `data:` shape | When |
|---|---|---|
| `total` | `{"total": <int>}` | Once, when the file count for the job is known. |
| `progress` | `{"processed", "total", "filename", "domain", "deduplicated"}` | Once per file processed. |
| `done` | `{"processed", "registered", "deduplicated"}` | Terminal success event. |
| `error` | `{"reason": "..."}` | Terminal failure event, or immediately if `jobId` is invalid/inaccessible. |

### Keepalive

A `": keepalive\n\n"` comment is sent after **25s** of no new events — this
was already in place before this pass (the audit's "~25s keepalives").

### Reconnection

The stream **replays every buffered event from the beginning** on each new
connection (`snapshot_from(0)` — up to 2000 events kept per job), then
switches to live tail. This means, unlike the other three endpoints, a
disconnect-and-reconnect here is safe and self-healing: the client will see
the full event history again and can simply re-render from scratch. Events
are evicted 300s after the job completes.

### Gateway

Route `data-loader-dataset-progress`, `response-timeout: -1`.

---

## Summary table

| Endpoint | Named events | Keepalive | Replay on reconnect |
|---|---|---|---|
| `POST /api/v1/query/stream` | No (unnamed `message`) | ~20s (added in G7.4) | No — re-`POST` |
| `GET /api/v1/documents/events` | Yes (`current_state`, `document_status`) | ~20s (added in G7.4) | No, but `current_state` on reconnect covers it |
| `GET /api/v1/documents/cleanup/jobs/{id}/events` | Yes (`cleanup_progress`, `cleanup_complete`) | None | Terminal-state only, if job already finished |
| `GET /api/v1/datasets/load/{id}/progress` | Yes (`total`, `progress`, `done`, `error`) | ~25s (pre-existing) | Full event history replayed from position 0 |
