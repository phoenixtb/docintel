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
  Embeddings   → LLM_EMBED_MODEL      (dense vectors via engine embed endpoint)
  Sparse (BM25)→ fastembed local       (no server, CPU, lightweight)
  Reranker     → RERANKER_MODEL        (cross-encoder, in-process sentence-transformers, MPS/CUDA/CPU)
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
    )

    # ── Qdrant ───────────────────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "documents"
    qdrant_cache_collection: str = "response_cache"
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
    llm_model: str = "qwen3.5:4b"
    llm_fallback_model: str = "phi3:mini"

    # Embeddings — dense vectors (1024-dim for qwen3-embed). Must match qdrant_embedding_dim.
    llm_embed_model: str = "qwen3-embed:0.6b:4bit"
    llm_embed_dim: int = 1024

    # LLM generation settings
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    # Fast model used only for async conversation summarization (not main queries).
    llm_expansion_model: str = "qwen3:1.7b"

    # ── Conversation context compression ──────────────────────────────────────
    # Anchored iterative summarization — compresses evicted turns into a rolling
    # summary, keeping only the last N messages verbatim for the LLM.
    conversation_summary_threshold: int = 8   # compress when total messages exceed this
    conversation_verbatim_recent: int = 4     # always keep last N messages verbatim

    # Context windows.
    #   system prompt + 5-10 retrieved chunks (~3k tokens) + history + question + answer.
    # Override via LLM_CTX / LLM_THINKING_CTX in .env.
    llm_ctx: int = 16384          # standard queries
    llm_thinking_ctx: int = 32768  # thinking mode (thinking block eats extra context)

    # ── Reranker — in-process sentence-transformers (MPS/CUDA/CPU) ───────────
    # LocalCrossEncoderRanker auto-selects device: mps → cuda → cpu.
    # Model is downloaded once and cached by HuggingFace Hub.
    # For NVIDIA TensorRT opt-in via Infinity: docker compose --profile infinity up
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    use_reranking: bool = True

    # ── RAG parameters ────────────────────────────────────────────────────────
    rag_retriever_top_k: int = 50
    rag_reranker_top_k: int = 10
    rag_default_top_k: int = 5
    rag_min_relevance_score: float = 0.0
    rag_cache_similarity_threshold: float = 0.92

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

    # ── Domain routing (optional, disabled by default) ────────────────────────
    rag_use_domain_routing: bool = False
    rag_domain_router_model: str = "MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33"
    rag_domain_routing_confidence: float = 0.6

    # ── Query expansion (optional, disabled by default) ───────────────────────
    use_query_expansion: bool = False

    # ── Langfuse tracing ──────────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # ── External services ─────────────────────────────────────────────────────
    document_service_url: str = "http://document-service:8081"
    analytics_service_url: str = "http://analytics-service:8001"
    postgres_url: str = "postgresql://docintel:docintel_secret@postgres:5432/docintel"

    # ── OPA ───────────────────────────────────────────────────────────────────
    opa_url: str = "http://opa:8181"

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
