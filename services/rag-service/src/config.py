"""
Service configuration — single source of truth for all env vars.

All model inference runs through an OpenAI-compatible endpoint.
Switch engines by updating LLM_CHAT_URL / LLM_EMBED_URL:
  LMForge (macOS/Apple Silicon):  LLM_CHAT_URL=http://host.docker.internal:11430/v1
                                   LLM_EMBED_URL=http://host.docker.internal:11430/v1
  Ollama (any platform):          LLM_CHAT_URL=http://host.docker.internal:11434/v1
                                   LLM_EMBED_URL=http://host.docker.internal:11434/v1
  vLLM (Linux/NVIDIA):            LLM_CHAT_URL=http://host:8000/v1
                                   LLM_EMBED_URL=http://host:8001/v1  (separate embed instance)
  LM Studio (Windows/macOS):      LLM_CHAT_URL=http://host.docker.internal:1234/v1

Model tiers:
  LLM          → LLM_MODEL            (chat generation, streaming)
  Embeddings   → LLM_EMBED_MODEL      (dense vectors via engine embed endpoint — LMForge/Ollama/vLLM)
  Sparse (BM25)→ fastembed local       (no server, CPU, lightweight)
  Reranker     → RERANKER_MODEL        (cross-encoder served by Infinity ONNX sidecar at RERANKER_URL)
  Domain router→ RAG_DOMAIN_ROUTER_MODEL (optional, disabled by default)
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,   # LLM_TOP_P="" → None, not a parse error
    )

    # ── Qdrant ───────────────────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    # Must match llm_embed_dim. Update both together when switching embed model.
    qdrant_embedding_dim: int = 1024
    # Set QDRANT_QUANTIZATION=false to disable INT8 scalar quantization (e.g. during benchmarking).
    qdrant_quantization: bool = True

    # ── LLM Engine — OpenAI-compatible endpoint ───────────────────────────────
    # Chat completions (generation, streaming, thinking mode)
    llm_chat_url: str = "http://host.docker.internal:11434/v1"  # Ollama default
    # Embeddings — same engine as chat by default; override via LLM_EMBED_URL
    llm_embed_url: str = "http://host.docker.internal:11430/v1"  # LMForge default
    # API key — ignored by local engines; set for hosted APIs (OpenAI, Anthropic, etc.)
    llm_api_key: str = "none"

    # LLM — chat generation and streaming
    llm_model: str = "qwen3.5:4b:6bit"
    llm_fallback_model: str = "phi3:mini"

    # Embeddings — dense vectors (1024-dim for qwen3-embed). Must match qdrant_embedding_dim.
    llm_embed_model: str = "qwen3-embed:0.6b:8bit"
    llm_embed_dim: int = 1024

    # LLM generation sampling parameters — global env-level fallbacks.
    # Resolution order at query time:
    #   tenant DB profile → platform DB profile → built-in code defaults → these values.
    # Override in .env (generated from config/defaults.env) to change the baseline.
    llm_temperature: float = 0.1
    llm_top_p: float | None = None        # LLM_TOP_P="" in defaults.env → use model default
    llm_max_tokens: int = 1024
    llm_frequency_penalty: float = 0.3
    llm_presence_penalty: float = 0.0
    llm_repetition_penalty: float | None = 1.05  # NULL → LMForge oMLX derives from f+p penalty
    llm_top_k: int | None = None
    llm_min_p: float | None = None
    llm_thinking_temperature: float = 0.6
    llm_thinking_top_p: float = 0.95
    llm_thinking_max_tokens: int = 6144
    llm_thinking_frequency_penalty: float = 0.0  # Qwen3 spec: 0 in thinking mode
    llm_thinking_presence_penalty: float = 0.3   # Qwen3 spec
    llm_thinking_repetition_penalty: float = 1.2  # explicit; wins over oMLX derived value
    llm_thinking_top_k: int = 20                 # Qwen3 spec
    llm_thinking_min_p: float = 0.0              # Qwen3 spec
    llm_thinking_budget: int = 4096              # LMForge two-call hard cap on reasoning tokens
    llm_stream_thinking: bool = True             # send stream_reasoning_deltas to LMForge
    # HTTP stream timeout (seconds). Haystack/OpenAI client default is 30s which
    # is far too short for thinking mode where the first token may take >60s.
    llm_stream_timeout_s: float = 120.0          # normal mode
    llm_thinking_stream_timeout_s: float = 360.0 # thinking mode (6 min for complex reasoning)
    llm_stream_max_retries: int = 1              # retries for normal mode
    llm_thinking_stream_max_retries: int = 0     # no retry for thinking — already waited long
    # Fast model used for async conversation summarization AND query expansion
    # (G2). Must be a chat model actually served by the engine — "qwen3:1.7b"
    # (no quant suffix) 404s against LMForge, which serves "qwen3:1.7b:4bit".
    llm_expansion_model: str = "qwen3:1.7b:4bit"

    # ── Conversation context compression ──────────────────────────────────────
    # Anchored iterative summarization — compresses evicted turns into a rolling
    # summary, keeping only the last N messages verbatim for the LLM.
    conversation_summary_threshold: int = 8   # compress when total messages exceed this
    conversation_verbatim_recent: int = 4     # always keep last N messages verbatim

    # ── Reranker — LMForge /v1/rerank (oMLX on Mac; replaces Infinity sidecar) ─
    # Qwen3-Reranker cross-encoder (MLX). 0.6b:8bit = fast on-device default;
    # 4b:8bit is the quality tier. jina/bge are not published in MLX format.
    reranker_model: str = "qwen3-reranker:0.6b:8bit"
    reranker_url: str = "http://host.docker.internal:11430/v1"
    use_reranking: bool = True

    # ── RAG parameters ────────────────────────────────────────────────────────
    rag_retriever_top_k: int = 50
    rag_reranker_top_k: int = 10
    rag_default_top_k: int = 5
    # Abstention gate — calibrated for qwen3-reranker sigmoid scores (0.5 = neutral):
    # noise/off-topic chunks score ~0.50-0.51, relevant chunks >=0.62. Recalibrate
    # via tests/integration/calibrate_threshold.py when the reranker model changes.
    rag_min_relevance_score: float = 0.55
    # When no doc passes rag_min_relevance_score, how many top docs to return anyway.
    # 0 = strict (return empty → NO_RELEVANT_DOCUMENTS_RESPONSE).
    # Set to 1 to always return at least one result regardless of score.
    rag_min_score_fallback_topk: int = 0

    # G5 — conditional reranking: skip the LMForge rerank round-trip when the
    # top RRF-fused hybrid score already clears a calibrated confidence margin.
    # rag_rerank_skip_min_score is on the FUSED-SCORE scale, NOT the reranker
    # scale that rag_min_relevance_score above is calibrated on — the two
    # thresholds must never be swapped or compared directly (see the guard
    # comment in pipelines/query.py step 7).
    #
    # OFF BY DEFAULT — calibration evidence (golden-set fused-score sample via
    # the rerank_gate log line, n=29): legitimate abstain-worthy queries reach
    # fused scores up to 0.83, and several genuinely-answerable queries sit as
    # low as 0.50 — the two distributions fully overlap across [0.50, 0.83].
    # No threshold in that band separates them; skipping there measurably
    # regressed abstention-correct (28/31 -> 25/31 at 0.75) by returning
    # unfiltered fused-order docs for queries that should have abstained. Only
    # a narrow band above 0.83 (e.g. >=0.9) showed separation in this small
    # 31-query sample, but with too few queries clearing it (~7%) to justify
    # the risk on unproven statistics. rag_rerank_skip_min_score defaults above
    # the observed max (1.0) so an accidental enable is a no-op until this is
    # recalibrated on a larger production sample. Calibration evidence in the
    # G5 commit message (git log --grep G5); methodology per plan 05 G5.
    rag_rerank_skip_enabled: bool = False
    rag_rerank_skip_min_score: float = 1.01

    rag_cache_similarity_threshold: float = 0.92
    # Typewriter replay for cache hits: chunk the cached response into small
    # TokenEvents to preserve the streaming UX. Set delay_ms=0 for instant replay.
    rag_cache_replay_chunk_chars: int = 24
    rag_cache_replay_chunk_delay_ms: int = 15

    # ── Hybrid search (BM25 sparse via fastembed, always local) ──────────────
    rag_use_hybrid_search: bool = True

    # ── Concurrency ───────────────────────────────────────────────────────────
    # Max concurrent LLM generation tasks across ALL tenants.
    # Local engines (single GPU, serial): 2-4 is optimal.
    # Hosted APIs (OpenAI, Anthropic): 20-50+ (rate-limited server-side by TPM/RPM).
    # vLLM with tensor-parallelism: gpu_count * 4 or higher.
    llm_concurrency_limit: int = 3

    # ── Semantic caching ──────────────────────────────────────────────────────
    use_cache: bool = True

    # ── Domain routing ────────────────────────────────────────────────────────
    rag_use_domain_routing: bool = True
    rag_domain_router_model: str = "MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33"
    rag_domain_routing_confidence: float = 0.6

    # ── Query expansion (G2, optional, disabled by default pending A/B) ──────
    use_query_expansion: bool = False
    # Hard wall-clock budget for the expansion LLM call. Expansion fails open
    # (falls back to the original query) on timeout — never blocks retrieval.
    rag_query_expansion_timeout_s: float = 2.0

    # ── Langfuse tracing ──────────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # ── External services ─────────────────────────────────────────────────────
    document_service_url: str = "http://document-service:8081"
    postgres_url: str = "postgresql://docintel:docintel_secret@postgres:5432/docintel"

    # ── OPA ───────────────────────────────────────────────────────────────────
    opa_url: str = "http://opa:8181"

    # ── Redis Streams (A7 — query telemetry producer, replaces HTTP POST to
    # analytics-service; see docintel_common.messaging.TOPIC_ANALYTICS_QUERY) ──
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str | None = None

    # ── Inter-service auth ────────────────────────────────────────────────────
    # HMAC key shared with the API Gateway. Set by setup.sh, injected via docker-compose.
    internal_gateway_secret: str = ""

    # ── Service metadata ──────────────────────────────────────────────────────
    service_version: str = "0.1.0"

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
