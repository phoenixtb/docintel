# Contract — reranker (rag-service → LMForge `POST /v1/rerank`)

What rag-service relies on from LMForge, and what it does when that fails. Client:
`services/rag-service/src/components/reranker.py`; gate: `src/pipelines/query.py` step 8.

## Request

```json
{ "model": "qwen3-reranker:0.6b:8bit", "query": "...", "documents": ["...", "..."], "top_n": 10 }
```

`RERANKER_URL` (default `http://host.docker.internal:11430/v1`) and `RERANKER_MODEL` select the
server and model.

## Response (200)

| Field | Meaning | Relied on |
|---|---|---|
| `score_type` | must be `"probability"` | anything else (or absent) is treated as **degraded**. Older LMForge squeezed probabilities into [0.5, 0.73] without declaring it, and misreading that scale is what made every threshold wrong |
| `results[].index` | position in the request's `documents` | yes |
| `results[].relevance_score` | relevance probability in [0, 1] | yes: compared with `rag_min_relevance_score` |
| `meta.truncated_documents` | indexes of documents cut to fit the model | logged only |

## Errors

| Status / `error.code` | Cause | rag-service |
|---|---|---|
| 400 `query_too_long` | the query alone fills the model window | degraded, reason `query_too_long` |
| 400 `input_too_long` | an input exceeds what the model can hold | degraded, reason `input_too_long` |
| 422 `reranker_unusable` | the installed reranker file is broken | degraded, reason `reranker_unusable` |
| other non-200 | anything else | degraded, reason `http_<status>` |
| transport error / timeout | LMForge down or slow | degraded, reason `unavailable` |

## Degraded behaviour

- **Order.** Documents keep their retrieval (RRF-fused) order and scores.
- **No relevance gate.** `rag_min_relevance_score` is **not** applied. It is calibrated on
  reranker probabilities, and fused scores cannot separate answerable from absent (G5 calibration
  in `src/config.py`).
- **Answering.** Generation's grounding rule handles information the documents do not contain.
- **Signals.** `reranker_degraded = true` is emitted in the stream metadata, and the web UI shows
  "Relevance scores unranked (reranker unavailable)".
- **Never** silent abstention.

The same "no tau on fused scores" rule applies when a request sets `use_reranking: false` and when
the reranker round-trip is skipped (G5).

## Calibration

`rag_min_relevance_score` is calibrated on this probability scale with
`tests/integration/calibrate_threshold.py`: a `--retrieval-only` harness run with
`RAG_MIN_RELEVANCE_SCORE=0` on the harness's seeded corpus.

The two engines produce different probability scales for the same model (llama.cpp GGUF Q8 vs
oMLX mxfp8), so calibrate on both. Recalibrate whenever the reranker model, its quantisation or
the LMForge engine changes.
