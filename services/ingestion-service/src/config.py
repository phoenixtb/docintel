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

    # --- Ollama ---
    ollama_base_url: str = Field(default="http://host.docker.internal:11434", alias="OLLAMA_BASE_URL")
    ollama_embedding_model: str = Field(default="nomic-embed-text", alias="OLLAMA_EMBEDDING_MODEL")

    # --- PostgreSQL ---
    database_url: str = Field(
        default="postgresql://docintel:docintel@postgres:5432/docintel",
        alias="DATABASE_URL",
    )

    # --- Document service (status callback) ---
    document_service_url: str = Field(
        default="http://document-service:8081",
        alias="DOCUMENT_SERVICE_URL",
    )

    # --- Docling ---
    docling_artifacts_path: str = Field(
        default="/app/docling-cache",
        alias="DOCLING_ARTIFACTS_PATH",
    )
    docling_do_ocr: bool = Field(default=True, alias="DOCLING_DO_OCR")
    docling_do_table_structure: bool = Field(default=True, alias="DOCLING_DO_TABLE_STRUCTURE")

    # --- Ingestion pipeline ---
    # Maximum workers for CPU-bound Docling execution
    docling_max_workers: int = Field(default=2, alias="DOCLING_MAX_WORKERS")

    # Minimum content length (chars) for a chunk to be indexed
    min_chunk_chars: int = Field(default=20, alias="MIN_CHUNK_CHARS")

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
