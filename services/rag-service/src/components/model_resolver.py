"""
TenantModelResolver
===================
Resolves the effective LLM model for a given tenant at query time.

Resolution hierarchy (first non-null wins):
  1. platform_settings.llm_model  — platform-wide override (null = "Tenant Choice")
  2. tenants.settings->>'llm_model' — per-tenant preference
  3. default_model                  — config fallback (OLLAMA_LLM_MODEL env var)

Results are cached in-process for TTL seconds to avoid a DB round-trip per query.
The cache is invalidated automatically on expiry; model changes are picked up within
TTL seconds without any explicit invalidation signal needed.
"""

import asyncio
import logging
import time
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_SENTINEL = object()  # distinguish "not cached" from a cached None


class TenantModelResolver:
    """
    Async-friendly resolver with an in-process TTL cache.

    Uses psycopg2 (already in deps) via run_in_executor so the event loop
    is never blocked. A single instance should be created at app startup
    and shared across requests.
    """

    # Class-level caches: tenant_id → (model, expires_at)
    _cache: dict[str, tuple[str, float]] = {}
    # Platform-level: (model|None, expires_at)  None = "Tenant Choice"
    _platform_cache: tuple[object, float] = (_SENTINEL, 0.0)

    TTL: float = 60.0

    def __init__(self, postgres_url: str, default_model: str) -> None:
        self._postgres_url = postgres_url
        self._default_model = default_model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve(self, tenant_id: str) -> str:
        """Return the effective LLM model name for tenant_id."""
        platform_model = await self._get_platform_model()
        if platform_model is not None:
            return platform_model

        model, expires_at = self._cache.get(tenant_id, (_SENTINEL, 0.0))
        if model is not _SENTINEL and time.monotonic() < expires_at:
            return model  # type: ignore[return-value]

        resolved = await asyncio.get_running_loop().run_in_executor(
            None, self._fetch_tenant_model_sync, tenant_id
        )
        self._cache[tenant_id] = (resolved, time.monotonic() + self.TTL)
        return resolved

    def invalidate(self, tenant_id: Optional[str] = None) -> None:
        """
        Invalidate cached entries.
          - tenant_id=None  → clear everything (platform model changed).
          - tenant_id=<id>  → clear only that tenant's entry.
        """
        if tenant_id is None:
            self._cache.clear()
            self.__class__._platform_cache = (_SENTINEL, 0.0)
        else:
            self._cache.pop(tenant_id, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_platform_model(self) -> Optional[str]:
        """Fetch/cache the platform-level model override. None = Tenant Choice."""
        cached_val, expires_at = self.__class__._platform_cache
        if cached_val is not _SENTINEL and time.monotonic() < expires_at:
            return cached_val if isinstance(cached_val, str) else None  # type: ignore[return-value]

        result = await asyncio.get_running_loop().run_in_executor(
            None, self._fetch_platform_model_sync
        )
        self.__class__._platform_cache = (result if result is not None else None, time.monotonic() + self.TTL)
        return result

    def _fetch_platform_model_sync(self) -> Optional[str]:
        try:
            with psycopg2.connect(self._postgres_url) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(
                        "SELECT value FROM platform_settings WHERE key = 'llm_model'"
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None
                    # psycopg2 decodes JSONB to Python native types:
                    # JSON null → None, JSON "string" → str
                    val = row["value"]
                    return val if isinstance(val, str) else None
        except Exception as exc:
            logger.warning("Failed to fetch platform model setting: %s", exc)
            return None

    def _fetch_tenant_model_sync(self, tenant_id: str) -> str:
        try:
            with psycopg2.connect(self._postgres_url) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(
                        "SELECT settings->>'llm_model' AS llm_model FROM tenants WHERE id = %s",
                        (tenant_id,),
                    )
                    row = cur.fetchone()
                    if row and row["llm_model"]:
                        return row["llm_model"]
        except Exception as exc:
            logger.warning(
                "Failed to fetch tenant model for %s: %s — using default", tenant_id, exc
            )
        return self._default_model
