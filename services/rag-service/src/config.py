"""
Service configuration — single source of truth for all env vars.

All model inference runs through Ollama (GPU/Metal on Apple Silicon, vLLM-compatible in prod).
To switch to vLLM or any OpenAI-compatible endpoint, update OLLAMA_BASE_URL and model names.

Model tiers:
  LLM          → OLLAMA_LLM_MODEL         (chat generation, streaming)
  Embeddings   → OLLAMA_EMBED_MODEL       (dense vectors, Metal-accelerated via Ollama)
  Sparse (BM25)→ fastembed local          (no server, CPU, lightweight)
  Reranker     → RERANKER_MODEL           (cross-encoder, CPU-local — no Ollama reranker API)
  Domain router→ RAG_DOMAIN_ROUTER_MODEL  (optional, disabled by default)
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
    # Must match ollama_embed_dim. Update both together when switching embed model.
    qdrant_embedding_dim: int = 768

    # ── Ollama (all inference runs through Ollama) ────────────────────────────
    # In prod: point to vLLM or any OpenAI-compatible endpoint via these env vars.
    ollama_base_url: str = "http://host.docker.internal:11434"

    # LLM — chat generation and streaming
    ollama_llm_model: str = "qwen3:4b"
    ollama_llm_fallback: str = "phi3:mini"

    # Embeddings — dense vectors (768-dim). Must match qdrant_embedding_dim.
    ollama_embed_model: str = "nomic-embed-text"
    ollama_embed_dim: int = 768

    # LLM generation settings
    ollama_llm_temperature: float = 0.1
    ollama_llm_max_tokens: int = 1024

    # ── Reranker ─────────────────────────────────────────────────────────────
    # Runs locally on CPU. Haystack has no Ollama reranker component.
    # For prod: swap to Cohere/Jina reranker API or Infinity server.
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

    # ── Semantic caching ──────────────────────────────────────────────────────
    use_cache: bool = True

    # ── Domain routing (optional, disabled by default) ────────────────────────
    rag_use_domain_routing: bool = False
    rag_domain_router_model: str = "MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33"
    rag_domain_routing_confidence: float = 0.6

    # ── Query expansion (optional, disabled by default) ───────────────────────
    use_query_expansion: bool = False
    ollama_expansion_model: str = "qwen3:1.7b"

    # ── Langfuse tracing ──────────────────────────────────────────────────────
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "http://localhost:3000"

    # ── External services ─────────────────────────────────────────────────────
    document_service_url: str = "http://document-service:8081"
    postgres_url: str = "postgresql://docintel:docintel_secret@postgres:5432/docintel"

    # ── Service metadata ──────────────────────────────────────────────────────
    service_version: str = "0.1.0"

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def litellm_model(self) -> str:
        """LiteLLM-prefixed model name (for any litellm usage)."""
        return f"ollama/{self.ollama_llm_model}"

    @property
    def litellm_fallbacks_list(self) -> list[str]:
        return [f"ollama/{self.ollama_llm_fallback}"]

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
