"""Data loader service configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- MinIO ---
    minio_url: str = Field(default="http://minio:9000", alias="MINIO_URL")
    minio_access_key: str = Field(default="minioadmin", alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="minioadmin", alias="MINIO_SECRET_KEY")

    # --- Document service ---
    document_service_url: str = Field(
        default="http://document-service:8081",
        alias="DOCUMENT_SERVICE_URL",
    )

    # --- Internal auth ---
    internal_gateway_secret: str = Field(default="", alias="INTERNAL_GATEWAY_SECRET")

    # --- Redis Streams ---
    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_password: str | None = Field(default=None, alias="REDIS_PASSWORD")

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
