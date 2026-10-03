# ingestion-service

Document ingestion pipeline: Docling parse → domain classify → BM25 + Ollama embed → Qdrant index → PG chunks.

`POST /ingest` is a debug/integration-test-only path (`INGESTION_REST_ENABLED=false` in prod) — production ingestion always runs through the `documents.ready` Redis stream consumer (`stream_worker.py`).
