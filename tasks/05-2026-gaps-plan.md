# Plan 05 — Closing the 2026-Baseline Gaps

Status: DRAFT — review before implementation.
Executor: Sonnet 5 subagent(s), one phase per agent, reviewed before merge.
Deferred-by-design items (GraphRAG, agentic retrieval): see `docs/future_features/graphrag-agentic-rag.md`.

Ordered by measured-quality impact per unit of effort.

---

## G1. Fix the golden set (prerequisite for everything else)

- **Problem:** harness pass-rate (8/21) is dominated by corpus sparsity, not pipeline quality — only 5 HR docs seeded, so 6 "answerable" HR queries have no covering document and correctly abstain but count as failures. A KPI nobody trusts gates nothing.
- **Fix:**
  1. Raise `samples_per_dataset` for HR (and technical) in `setup_e2e_environment` so every `expect_abstention: false` query has a covering doc — verify per-query with `relevant_docs` hits; OR relabel uncoverable queries `expect_abstention: true`.
  2. Add 3–5 near-miss abstention queries per domain (the WFH pattern: plausible topic, no covering doc).
  3. Re-run tau calibration (`calibrate_threshold.py`) on a tau=0 pass after the corpus change; update `RAG_MIN_RELEVANCE_SCORE` if the gap moves.
- **Acceptance:** every failure in a full `--metrics` run is a genuine pipeline deficiency; pass-rate becomes the release KPI.

## G2. Wire QueryExpander (query rewriting)

- **Problem:** `services/rag-service/src/components/query_transform.py` exists; `use_query_expansion=False`; never called in `pipelines/query.py`. Query rewriting is 2026 table-stakes and attacks the observed weak-retrieval class (conversational phrasing, vocabulary mismatch).
- **Fix:**
  1. Insert expansion between cache-check and retrieval in `RAGService.stream`: expand → embed expanded + original → union candidates before rerank (reranker already handles precision).
  2. Use `llm_expansion_model` (qwen3:1.7b — small, fast) with a strict, few-token rewrite prompt; hard timeout ~2s, fail-open to the original query.
  3. Config flag default off → flip on after A/B via harness.
- **Acceptance:** harness A/B (flag off vs on) shows hit@5 / MRR improvement on the technical suite with p95 latency increase < 2.5s; no abstention-correctness regression.

## G3. CI evaluation gate

- **Problem:** quality is measured manually; regressions ship silently.
- **Fix:**
  1. Add threshold checking to `run_tests.py`: `--gate` flag reading minimums from `queries.yaml` config (suggested initial: hit@5 ≥ 0.9 on covered queries, faithfulness ≥ 0.8 avg, abstention-correct ≥ 0.9, zero reranker-degraded) → non-zero exit on breach.
  2. `.github/workflows/integration-tests.yml`: run with `--metrics --gate` (judge model must be available in CI — if no GPU runner, gate retrieval + abstention metrics only and skip LLM-judge in CI; full judge stays a local/nightly job).
  3. Persist report JSON as workflow artifact for trend comparison.
- **Acceptance:** a PR that degrades retrieval (e.g. breaks hybrid fusion) fails CI with the metric named in the log.

## G4. ragas adoption — decide and act

- **Problem:** `ragas==0.4.3` is a declared rag-service dependency but unused; custom judge in `tests/integration/metrics.py` duplicates faithfulness/relevancy.
- **Recommendation: adopt for the judge layer, keep our retrieval metrics.** ragas metrics are the industry benchmark (comparability, tuned prompts, answer-relevancy embedding variant); our hit@k/MRR/recall stay custom (they're trivial and corpus-specific). ragas supports custom OpenAI-compatible endpoints → points at LMForge judge model.
- **Fix:**
  1. Move `ragas` dep from rag-service (runtime — wrong place) to `tests/integration/pyproject.toml`.
  2. Replace `generation_judge` internals with ragas `faithfulness` + `answer_relevancy` wired to the LMForge judge; keep our abstention heuristic and the report schema unchanged.
  3. Fallback: if ragas + LMForge non-thinking mode fights us (score extraction), keep the custom judge and delete the unused dep — decision recorded in the PR.
- **Acceptance:** `--metrics` runs produce ragas-computed scores in the same report format; rag-service runtime image no longer ships ragas.

## G5. Conditional reranking (latency)

- **Problem:** every query pays the rerank round-trip; contracts queries run 15–27s E2E on-device.
- **Fix:** after RRF fusion, if the top candidate's fused score clears a confidence margin (both retrievers agree), skip rerank and tag `rerank_skipped=true` in metadata; otherwise rerank as today. Calibrate the skip threshold from harness score distributions (same methodology as tau).
- **Guard:** min-score gate (tau) is calibrated on *reranker* scores — when rerank is skipped, apply the gate on the fused-score scale with its own calibrated threshold, or force rerank whenever the gate would be decisive (scores near tau). Subagent must handle this interaction explicitly.
- **Acceptance:** harness quality metrics unchanged (hit@5, abstention-correct); measurable p50 latency drop on high-confidence queries; skip decisions visible in metadata + Prometheus counter.

## G6. Passage-level citations

- **Problem:** answers cite whole chunks; 2026 traceability expectation is passage-level ("answer identifies the exact passage").
- **Fix (incremental, not inline-marker rewriting):**
  1. Prompt: instruct the model to reference sources as `[1]`, `[2]` matching the numbered context chunks (already numbered in prompt builder).
  2. Stream/UI: parse `[n]` markers in the answer, link them to the source cards; highlight the cited chunk.
  3. Faithfulness judge already penalizes uncited claims — add citation-coverage to the report (fraction of answer sentences carrying a marker).
- **Acceptance:** rendered answers show clickable citation markers mapped to sources; citation-coverage metric in harness report.

## G7. API maturity for multi-client (Flutter)

Audit verdict (Aug 2026): the gateway surface is already Bearer-JWT + consistent `/api/v1` prefix with no cookie/CSRF coupling on API calls — fundamentally usable from Flutter. Blockers are identity provisioning, contract publication, and consistency, not architecture.

### G7-P0 — blockers (before any Flutter code)
1. **Native OIDC client in Zitadel.** Terraform (`terraform/modules/zitadel/project/main.tf`) defines only the SPA `USER_AGENT` app with localhost redirects. Add an `OIDC_APP_TYPE_NATIVE` app: PKCE, refresh tokens, custom-scheme redirect (`com.docintel.app:/callback`) + HTTPS app-link URIs; pin exact redirects, `dev_mode=false` outside dev. Token lifetimes (access 15m / refresh idle 24h) are fine for mobile with `flutter_appauth`.
2. **Aggregated OpenAPI at the gateway.** No spec exists today for the public surface (Kotlin services have no springdoc; FastAPI `/docs` are per-service, unrouted). Deliver one checked-in `/api/v1` OpenAPI (add springdoc to document/admin services, export FastAPI schemas, merge with path rewrites applied; publish via gateway route + commit to `docs/api/`). This is the codegen source for the Flutter client (`openapi-generator`/`dio`).
3. **One error envelope.** Today: FastAPI `{"detail"}` vs Kotlin `{"error","message"}` vs empty-body 429 with `X-Quota-Exceeded` header only. Standardize `{"error": {"code", "message", "request_id"}}` — `@ControllerAdvice` in Kotlin services + FastAPI exception handlers + a body on the gateway quota/rate-limit 429s.
4. **SSE client contract doc.** All four SSE endpoints require `Authorization` header (EventSource can't set it — web-ui already uses fetch-streaming; Flutter needs the same). Document per endpoint: event names, JSON keys, keepalive behavior (query stream and doc events have **no** heartbeat; dataset loader has ~25s keepalives), reconnection semantics (doc events send `id:`/`retry:` but no replay; query stream = re-POST). Add heartbeat comments to query-stream + doc-events while at it — idle mobile proxies drop silent connections.

### G7-P1 — strong recommendations
5. Align gateway base `application.yml` with the docker profile (models, datasets, SSE timeout routes) — local vs compose currently expose different surfaces.
6. Split public vs internal document routes: `from-path`, chunk bulk/append, data-source CRUD ride under the same `/api/v1/documents/**` glob — OPA-deny for non-service roles or move under `/internal`.
7. Naming: camelCase (Kotlin) vs snake_case (Python) forces dual client models — document as-is in the OpenAPI spec (do not churn both stacks now); revisit only if client pain is real.
8. Pagination: document both existing styles (Spring `Page` for documents, `limit/offset` for conversations); don't introduce a third.
9. Upload: verify gateway proxy body limit covers the 100MB document-service cap; note absence of resumable upload for mobile networks (defer implementation).

### G7-P2 — defer (note in future_features if wanted)
- Original-file download API, push notification hooks, ETag/offline caching, richer 429 bodies.

- **Acceptance:** a Flutter dev can authenticate (native PKCE), generate a typed client from the committed OpenAPI spec, stream a query answer, and upload a document — without reading any server source code.

---

## Sequencing

```
G1 (golden set)  ──►  G3 (CI gate — needs trustworthy KPI)
G2 (query expansion, A/B measured against G1 baseline)
G4 (ragas) — independent, any time
G5 (conditional rerank) — after G1 (needs calibration re-run)
G6 (citations) — independent
G7 (API maturity) — independent; prerequisite for Flutter client project
```

Suggested subagent batching: [G1+G3], [G2], [G4], [G5], [G6], [G7] — one PR each, harness run attached to every PR description.
