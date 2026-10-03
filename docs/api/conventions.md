# API Conventions

## Pagination

DocIntel has two pagination styles in production. A third style is **not**
being introduced — new endpoints should pick whichever of the two below fits.

### Style A: Spring Data `Page` (Kotlin services)

Used by: `GET /api/v1/documents` (document-service).

Query params: `page` (0-indexed, default `0`), `size` (default `20`, **no
configured max** — a client can request an arbitrarily large page), `sort`
(optional, e.g. `createdAt,desc`).

Response is the raw Spring Data `Page<T>` serialization (camelCase, no custom
wrapper):

```json
{
  "content": [ { "id": "...", "filename": "...", "...": "..." } ],
  "totalElements": 137,
  "totalPages": 7,
  "number": 0,
  "size": 20,
  "first": true,
  "last": false,
  "numberOfElements": 20,
  "empty": false,
  "pageable": { "pageNumber": 0, "pageSize": 20, "sort": { "...": "..." } },
  "sort": { "empty": true, "sorted": false, "unsorted": true }
}
```

A Flutter/mobile client should read `content`, `totalPages` (or `last`), and
`number` — the rest of the envelope (`pageable`, `sort`) is Spring Data
boilerplate and can be ignored.

### Style B: `limit`/`offset` and `page`/`page_size` (Python services)

Two params-naming variants exist, both conceptually the same LIMIT/OFFSET
pagination:

- **`limit`/`offset`** — `GET /api/v1/conversations` (rag-service).
  `limit` (default `50`), `offset` (default `0`), **no max clamp**. Response
  is a **bare JSON array** — no `total`, no `has_more`. A client that needs
  to know if more pages exist must request `limit+1` and check if it got
  more than `limit` items back, or just keep paging until an empty/shorter
  page is returned.

- **`page`/`page_size`** — `GET /api/v1/analytics/feedback` (analytics-service-py).
  `page` (0-indexed, default `0`), `page_size` (default `20`, **clamped to
  1–100**). Response is `{"page", "page_size", "total", "items"}` — `total`
  is a real `COUNT(*)`, so the client can compute total pages as
  `ceil(total / page_size)`. No `has_more` field either, but it's derivable.

`GET /api/v1/analytics/top-queries` is a **top-N**, not a paginated list: a
single `limit` param (default `20`, clamped 1–100) caps two result arrays
(`frequent_queries`, `zero_result_queries`) with no offset — there is no
"next page" for this endpoint by design.

### Unbounded list endpoints

The following return a plain array/object with no pagination at all. Each is
currently backed by a naturally small collection (per-tenant model profiles,
a single document's chunks, a fixed dataset catalog), so this is not a
correctness bug today, but a client should not assume these stay small
forever:

- `GET /api/v1/documents/{id}/chunks`, `GET /api/v1/documents/data-sources` (document-service)
- `GET /api/v1/admin/tenants`, `GET /api/v1/tenants/{id}/users`, `GET /api/v1/admin/model-profiles`, `GET /api/v1/tenants/{id}/model-profiles` (admin-service)
- `GET /api/v1/conversations/{id}` — returns **all** messages, unpaginated (rag-service)
- `GET /api/v1/datasets` (data-loader) — static catalog, currently 3 entries

### Cursor-based pagination

Not used anywhere in DocIntel today.

---

## Upload limits

Document upload (`POST /api/v1/documents`, multipart) is capped at
**100MB** by document-service (`spring.servlet.multipart.max-file-size` /
`max-request-size` in `application.yml`; exceeding it now returns the
standard error envelope with `code: "PAYLOAD_TOO_LARGE"` — see
`GlobalExceptionHandler`, G7.3).

The gateway itself imposes **no additional, smaller limit**: no filter in
the request path reads or buffers the request body (multipart bytes are
proxied as a raw stream via Spring Cloud Gateway's Netty routing), and
neither `application.yml` nor `application-docker.yml` configures a
`spring.codec.max-in-memory-size` or Netty max-content-length override. So
the gateway does not need a matching "100MB" setting of its own — it simply
streams whatever document-service is willing to accept, and document-service
is the single source of truth for the cap.

### Resumable upload — not implemented

There is no chunked/resumable upload protocol (no `tus`-style or
range-based multipart-part upload). A dropped connection mid-upload means
starting the 100MB POST over from byte zero. This is a known gap for
mobile/Flutter clients on unreliable networks and is deferred — flagged here
rather than implemented as part of G7.
