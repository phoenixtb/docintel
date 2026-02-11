# DocIntel API Documentation

## Overview

DocIntel exposes a REST API through the API Gateway on port 8080.

## Authentication

All requests require a Bearer token in the Authorization header:

```
Authorization: Bearer <token>
```

For local development, use the demo token or disable auth in the gateway config.

---

## Endpoints

### Documents

#### Upload Document

```http
POST /api/v1/documents
Content-Type: multipart/form-data
```

**Request:**
- `file`: Document file (PDF, DOCX, TXT, MD)
- `metadata`: JSON object with document metadata

**Response:**
```json
{
  "id": "uuid",
  "filename": "policy.pdf",
  "status": "processing",
  "chunk_count": 0
}
```

#### List Documents

```http
GET /api/v1/documents?page=0&size=20&status=completed
```

#### Get Document

```http
GET /api/v1/documents/{id}
```

#### Delete Document

```http
DELETE /api/v1/documents/{id}
```

---

### Query

#### Query Documents

```http
POST /api/v1/query
Content-Type: application/json
```

**Request:**
```json
{
  "question": "What is the vacation policy?",
  "document_type": null,
  "top_k": 5,
  "use_cache": true
}
```

**Response:**
```json
{
  "answer": "Full-time employees are entitled to 20 days...",
  "sources": [
    {
      "document_id": "uuid",
      "filename": "vacation-policy.pdf",
      "chunk_index": 0,
      "score": 0.89
    }
  ],
  "detected_domain": "hr_policy",
  "cache_hit": false,
  "cost_usd": 0.0001
}
```

#### Streaming Query

```http
POST /api/v1/query/stream
Content-Type: application/json
Accept: text/event-stream
```

Returns Server-Sent Events with streaming response.

---

### Admin

#### Health Check

```http
GET /api/v1/admin/health
```

#### Clear Cache

```http
POST /api/v1/admin/cache/clear
```

#### Tenant Stats

```http
GET /api/v1/tenants/{id}/stats
```

---

## Error Responses

```json
{
  "error": "error_code",
  "message": "Human readable message",
  "details": {}
}
```

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `unauthorized` | 401 | Missing or invalid token |
| `forbidden` | 403 | Insufficient permissions |
| `not_found` | 404 | Resource not found |
| `rate_limited` | 429 | Too many requests |
| `internal_error` | 500 | Server error |
