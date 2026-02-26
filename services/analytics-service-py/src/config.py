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


@lru_cache
def get_settings() -> Settings:
    return Settings()
