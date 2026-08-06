# DocIntel — Private Document Intelligence for Your Organization

**DocIntel turns your organization's documents into a trustworthy, access-controlled AI knowledge base — running entirely on your own infrastructure.** Upload contracts, policies, and technical docs; ask questions in plain language; get streamed, cited answers that respect who is allowed to see what. No document, prompt, or answer ever leaves your network.

> Upload documents. Ask questions. Get grounded answers with sources — or an honest "I don't know" when the answer isn't there.

---

## Why teams choose DocIntel

| | Cloud RAG SaaS | **DocIntel (self-hosted)** |
|---|---|---|
| **Data privacy** | Documents indexed on vendor cloud | Everything on your hardware |
| **Access control** | Per-app, often UI-level | Chunk-level ABAC enforced at retrieval (OPA) |
| **Answer honesty** | Tuned for engagement | Calibrated abstention — refuses when evidence is missing |
| **Cost** | Per-seat + per-token | Local inference (LMForge); no per-token bills |
| **Multi-tenancy** | Shared index risk | Isolated per-tenant collections + row-level security |
| **Auditability** | Vendor logs | Full traces: retrieval, scores, generation (Langfuse/ClickHouse) |
| **Model choice** | Vendor's models | Any OpenAI-compatible engine; swap models per tenant |

**Who it's for:**
- **Regulated businesses** (legal, HR, finance, healthcare) that cannot ship documents to third-party clouds.
- **Enterprises with clearance levels** — answers must respect department, role, region, and document expiry.
- **Platform teams** who want RAG as an internal service with real observability and evaluation, not a black box.
- **Anyone running local LLMs** (LMForge, Ollama, vLLM) who wants a production document-QA stack on top.

---

## Architecture at a glance

```
Web UI (SvelteKit) ──► API Gateway (Spring Cloud, JWT + OPA RBAC)
                            │
     ┌──────────────┬───────┴────────┬──────────────┐
 Document Svc   RAG Service     Ingestion Svc   Admin Svc
 (Kotlin/JPA)   (FastAPI/       (FastAPI/       (Kotlin)
     │           Haystack)       Docling)           │
   MinIO        Qdrant + OPA    Redis Streams   Zitadel (SSO)
                LMForge (LLM/embed/rerank — local inference)
```

Event-driven ingestion (Redis Streams), per-tenant Qdrant collections, Postgres for conversations/metadata, ClickHouse + Langfuse + Prometheus/Grafana for telemetry.

---

## Features

### 1. Ask your documents — streamed, cited answers

**What:** Chat interface over your document corpus. Answers stream token-by-token (SSE) with source chunks, relevance scores, and the originating document attached.

**Why it matters:** Users see *why* an answer was given — which document, which passage, how relevant — not just fluent text.

**Try it:** upload a contract, ask "What are the termination clauses?" — the answer cites the exact chunks with per-source relevance percentages.

---

### 2. Honest abstention (calibrated "I don't know")

**What:** A score gate between retrieval and generation, calibrated against the reranker's measured score distribution. When no chunk clears the relevance bar, DocIntel says it doesn't have the information — and shows **zero** misleading sources.

**Why it matters:** The most damaging RAG failure is a confident answer built on irrelevant context. DocIntel refuses instead of hallucinating, and the threshold is derived from data (`calibrate_threshold.py`), not guessed.

**Try it:** ask about a policy that doesn't exist in the corpus — you get a clear "not in the available documents" with no fake sources.

---

### 3. Hybrid retrieval (semantic + keyword)

**What:** Every query runs dense vector search and sparse BM25 in parallel over Qdrant, fused with Reciprocal Rank Fusion.

**Why it matters:** Vectors catch paraphrases ("WFH" ≈ "remote work"); BM25 catches exact identifiers (clause numbers, product codes, names). Their union is the 2026 production baseline for a reason.

---

### 4. Cross-encoder reranking — with a degraded-mode signal

**What:** Retrieved candidates are re-scored by a dedicated reranker model (Qwen3-Reranker via LMForge `/v1/rerank`). If the reranker is ever unavailable, the pipeline falls back gracefully **and tells you**: an explicit `reranker_degraded` flag flows through the SSE stream, a Prometheus counter, and a UI badge.

**Why it matters:** Reranking lifts top-k precision 15–30%. Silent reranker failure is a classic silent-quality-loss bug — DocIntel makes it loud.

---

### 5. Chunk-level access control (ABAC via OPA)

**What:** Two policy layers, both fail-closed: route-level RBAC at the gateway, and per-chunk ABAC after retrieval — clearance level, roles, allowed users, department, region, document expiry — evaluated by Open Policy Agent.

**Why it matters:** Access control enforced at the retrieval layer (not the UI) is the line between demo-grade and enterprise-grade RAG. Two users asking the same question get answers built only from chunks each is cleared to see.

**Try it:** tag a document with a clearance level, query as a user without it — those chunks never reach the LLM.

---

### 6. Enterprise SSO and multi-tenancy

**What:** Zitadel OIDC for login (web PKCE flow), JWT claims enriched with tenant, roles, and clearance via Actions. Per-tenant Qdrant collections, tenant-scoped storage buckets, tenant-aware conversation history.

**Why it matters:** Drop-in identity for organizations; tenants are isolated at the data layer, not by convention.

---

### 7. Smart document ingestion (Docling + OCR routing)

**What:** Event-driven pipeline: Docling parsing, per-page routing between digital-text extraction, layout analysis, Tesseract OCR, and VLM escalation for scanned/complex pages; token-aware chunking (512 tokens, 64 overlap) or Docling hybrid chunking for sharded PDFs; dense + sparse embedding per chunk.

**Why it matters:** Real corpora are messy — scanned contracts, tables, mixed layouts. Page-level routing spends expensive OCR/VLM effort only where needed.

**Try it:** upload a scanned PDF and a clean text file; both become queryable, with per-document progress streamed live to the UI.

---

### 8. Domain routing

**What:** A zero-shot classifier routes each query to its document domain (HR policy / technical / contracts), narrowing retrieval before it starts.

**Why it matters:** Cross-domain noise is a precision killer — "termination" means different things in an HR handbook and a vendor contract.

---

### 9. Semantic caching

**What:** Answers are cached by query *meaning* (embedding similarity ≥ 0.92), not exact string match. Cache hits replay with the same typewriter streaming UX.

**Why it matters:** Repeated questions — the majority in internal knowledge bases — return in milliseconds without touching the LLM.

---

### 10. Conversations with rolling summarization

**What:** Multi-turn conversations persisted per tenant. Older turns compress into an anchored rolling summary; the last N turns stay verbatim — bounded context, unbounded conversation length.

**Why it matters:** Follow-up questions work without blowing the context window or degrading answer quality over long sessions.

---

### 11. Reasoning ("thinking") mode

**What:** First-class support for reasoning models: per-request thinking toggle, thinking-token budget caps, live reasoning stream separated from the final answer, and distinct sampling profiles for thinking vs plain chat.

**Why it matters:** Hard analytical questions (contract cross-references, policy interplay) benefit from visible chain-of-thought — with a budget knob so latency stays controlled.

---

### 12. Fully local inference

**What:** All models — chat, embeddings, reranking, VLM — served by LMForge on your own hardware (Apple Silicon via oMLX, NVIDIA/AMD via llama.cpp/vLLM). Any OpenAI-compatible endpoint works; models are configurable per deployment and per tenant (model profiles in Admin).

**Why it matters:** Zero per-token cost, zero data egress, offline-capable. The whole stack runs on a single capable workstation.

---

### 13. Built-in quality evaluation

**What:** An integration harness that measures the pipeline like a product: retrieval metrics (hit@k, MRR, context recall), LLM-judge generation metrics (faithfulness, answer relevancy), abstention correctness, and a calibration tool that derives the abstention threshold from real score distributions. Reports in JSON + Markdown.

**Why it matters:** "Evaluation-first" is the 2026 enterprise standard — you tune retrieval with evidence, gate releases on measured quality, and catch regressions before users do.

**Try it:** `cd tests/integration && uv run python run_tests.py --metrics`

---

### 14. Deep observability

**What:** Prometheus metrics (query latency, cache hit/miss, reranker degradation, indexing throughput), Grafana dashboards, per-query Langfuse traces (retrieval → rerank → generation), and a ClickHouse analytics store with user feedback capture.

**Why it matters:** When an answer is wrong you can see *which stage* failed — retrieval, ranking, or generation — instead of guessing at prompts.

---

### 15. Admin console

**What:** Tenant management, user administration (via Zitadel), per-tenant model profiles and sampling parameters, active-models panel, platform settings.

**Why it matters:** Operating the platform is a UI task, not a config-file archaeology exercise.

---

## A 10-minute evaluation

1. **Start the stack:** `./scripts/docintel.sh` (starts infra, services, and checks your local LLM engine).
2. **Log in** via Zitadel SSO at the web UI.
3. **Seed sample data:** load the bundled datasets (HR policies, technical Q&A, CUAD contracts) via the data-loader.
4. **Ask a contract question** — watch the streamed answer with cited, scored sources.
5. **Ask something the corpus can't answer** — verify the honest abstention with zero sources.
6. **Run the harness:** `cd tests/integration && uv run python run_tests.py --metrics` — see hit@k, MRR, faithfulness scores.
7. **Open Grafana** (`:3002`) and **Langfuse** (`:3000`) — inspect the full trace of your queries.

---

## In one sentence

**DocIntel is a self-hosted, multi-tenant document-intelligence platform that combines hybrid retrieval, cross-encoder reranking, calibrated abstention, and chunk-level access control with fully local inference — so your organization gets grounded, auditable answers from its documents without a single byte leaving your network.**
