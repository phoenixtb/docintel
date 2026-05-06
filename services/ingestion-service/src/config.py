"""Ingestion service configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- MinIO ---
    minio_url: str = Field(default="http://minio:9000", alias="MINIO_URL")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")

    # --- Qdrant ---
    qdrant_url: str = Field(default="http://qdrant:6333", alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")

    # --- LLM Engine (OpenAI-compatible) ---
    # Embeddings endpoint — LMForge by default
    llm_embed_url: str = Field(default="http://host.docker.internal:11430/v1", alias="LLM_EMBED_URL")
    llm_embed_model: str = Field(default="qwen3-embed:0.6b:8bit", alias="LLM_EMBED_MODEL")
    llm_embed_dim: int = Field(default=1024, alias="LLM_EMBED_DIM")
    llm_api_key: str = Field(default="none", alias="LLM_API_KEY")

    # VLM (escalation path — multimodal OCR for bitmap-heavy / complex pages)
    llm_vlm_url: str = Field(default="http://host.docker.internal:11430/v1", alias="LLM_VLM_URL")
    llm_vlm_model: str = Field(default="qwen2.5-vl:3b:4bit", alias="LLM_VLM_MODEL")
    vlm_max_concurrency: int = Field(default=2, alias="VLM_MAX_CONCURRENCY")
    vlm_render_dpi: int = Field(default=150, alias="VLM_RENDER_DPI")

    # --- Document service (chunk persist + status callback) ---
    document_service_url: str = Field(
        default="http://document-service:8081",
        alias="DOCUMENT_SERVICE_URL",
    )
    internal_gateway_secret: str = Field(
        default="",
        alias="INTERNAL_GATEWAY_SECRET",
    )

    # --- PostgreSQL (read-only access to admin.model_profiles for VLM sampling) ---
    # docintel_documents role has SELECT on admin.model_profiles via init.sql grant.
    postgres_url: str = Field(
        default=(
            "postgresql://docintel_documents:docintel_documents_secret"
            "@postgres:5432/docintel"
        ),
        alias="POSTGRES_URL",
    )

    # --- Docling ---
    docling_artifacts_path: str = Field(
        default="/app/docling-cache",
        alias="DOCLING_ARTIFACTS_PATH",
    )
    docling_do_ocr: bool = Field(default=False, alias="DOCLING_DO_OCR")

    # Feature flag: enable per-page router (Phase 2/3). Set to True after smoke tests pass.
    ingestion_use_page_routing: bool = Field(default=False, alias="INGESTION_USE_PAGE_ROUTING")
    docling_do_table_structure: bool = Field(default=True, alias="DOCLING_DO_TABLE_STRUCTURE")

    # --- Ingestion pipeline ---
    # Maximum concurrent documents processed (asyncio.Semaphore + ProcessPoolExecutor size)
    docling_max_workers: int = Field(default=2, alias="DOCLING_MAX_WORKERS")

    # Pages per shard for memory-bounded PDF processing (Phase 3)
    docling_shard_pages: int = Field(default=25, alias="DOCLING_SHARD_PAGES")

    # Embedding batch size (chunks per OpenAI API call)
    embedding_batch_size: int = Field(default=32, alias="EMBEDDING_BATCH_SIZE")

    # Minimum content length (chars) for a chunk to be indexed
    min_chunk_chars: int = Field(default=20, alias="MIN_CHUNK_CHARS")

    # Enable REST /ingest endpoint (test/debug only; disabled in production)
    ingestion_rest_enabled: bool = Field(default=False, alias="INGESTION_REST_ENABLED")

    # --- Redis Streams ---
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: str | None = Field(default=None, alias="REDIS_PASSWORD")

    # Disable stream consumer in local dev / tests
    stream_consumer_enabled: bool = Field(default=True, alias="STREAM_CONSUMER_ENABLED")

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
