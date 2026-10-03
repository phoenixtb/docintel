from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    clickhouse_host: str = "clickhouse"
    clickhouse_port: int = 8123
    clickhouse_user: str = "clickhouse"
    clickhouse_password: str = "clickhouse"
    clickhouse_database: str = "docintel_analytics"

    service_version: str = "0.1.0"

    # ── Redis Streams consumer (A7 — query telemetry) ──────────────────────────
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str | None = None
    # Flush to ClickHouse every N buffered events or T seconds, whichever first.
    analytics_consumer_batch_size: int = 200
    analytics_consumer_flush_interval_s: float = 5.0
    # XAUTOCLAIM idle threshold — a worker that crashed mid-batch (after
    # XREADGROUP, before ack) leaves messages in the PEL; reclaim after this.
    analytics_consumer_claim_idle_ms: int = 300_000

    # ── B4 — Langfuse trace deep-links ─────────────────────────────────────────
    # Browser-reachable Langfuse URL (distinct from any internal service DNS
    # name) used to build clickable trace links in the Insights UI.
    langfuse_public_host: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
