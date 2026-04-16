"""
TenantModelResolver
===================
Resolves the effective LLM model and thinking_mode for a given (tenant, user) at query time.

Resolution hierarchy (first non-null wins):
  Model:
    1. platform_settings.llm_model   — platform-wide override (null = "Tenant Choice")
    2. tenants.settings->>'llm_model' — per-tenant preference
    3. default_model                  — config fallback (OLLAMA_LLM_MODEL env var)

  Thinking mode (user-scoped):
    1. user_preferences (key='thinking_mode') — per-user, per-tenant preference
    2. false                                  — default

Results are cached in-process for TTL seconds to avoid a DB round-trip per query.
Cache key is (tenant_id, user_id) for user-scoped entries.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_SENTINEL = object()  # distinguish "not cached" from a cached None


@dataclass(frozen=True)
class TenantResolved:
    model: str
    thinking_mode: bool


class TenantModelResolver:
    """
    Async-friendly resolver with an in-process TTL cache.

    Uses psycopg2 (already in deps) via run_in_executor so the event loop
    is never blocked. A single instance should be created at app startup
    and shared across requests.
    """

    # (tenant_id, user_id) → (TenantResolved, expires_at)
    _cache: dict[tuple[str, str], tuple[object, float]] = {}
    # Platform-level: (model|None, expires_at)
    _platform_cache: tuple[object, float] = (_SENTINEL, 0.0)

    TTL: float = 60.0

    def __init__(self, postgres_url: str, default_model: str) -> None:
        self._postgres_url = postgres_url
        self._default_model = default_model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve(self, tenant_id: str, user_id: str) -> TenantResolved:
        """Return the effective model and thinking_mode for (tenant_id, user_id)."""
        platform_model = await self._get_platform_model()

        cache_key = (tenant_id, user_id)
        cached_val, expires_at = self._cache.get(cache_key, (_SENTINEL, 0.0))
        if cached_val is not _SENTINEL and time.monotonic() < expires_at:
            resolved: TenantResolved = cached_val  # type: ignore[assignment]
            if platform_model is not None:
                return TenantResolved(model=platform_model, thinking_mode=resolved.thinking_mode)
            return resolved

        model_resolved = await asyncio.get_running_loop().run_in_executor(
            None, self._fetch_tenant_model_sync, tenant_id
        )
        thinking_mode = await asyncio.get_running_loop().run_in_executor(
            None, self._fetch_user_preferences_sync, tenant_id, user_id
        )

        resolved = TenantResolved(model=model_resolved, thinking_mode=thinking_mode)
        self._cache[cache_key] = (resolved, time.monotonic() + self.TTL)

        if platform_model is not None:
            return TenantResolved(model=platform_model, thinking_mode=resolved.thinking_mode)
        return resolved

    def invalidate(self, tenant_id: Optional[str] = None, user_id: Optional[str] = None) -> None:
        """
        Invalidate cached entries.
          - both None             → clear everything (platform model changed).
          - tenant_id only        → clear all users in that tenant.
          - tenant_id + user_id   → clear only that user's entry.
        """
        if tenant_id is None:
            self._cache.clear()
            self.__class__._platform_cache = (_SENTINEL, 0.0)
        elif user_id is not None:
            self._cache.pop((tenant_id, user_id), None)
        else:
            # Clear all (tenant_id, *) entries when model-level settings change
            keys_to_remove = [k for k in self._cache if k[0] == tenant_id]
            for k in keys_to_remove:
                del self._cache[k]

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
                    cur.execute("SET LOCAL app.user_role = 'platform_admin'")
                    cur.execute(
                        "SELECT value FROM admin.platform_settings WHERE key = 'llm_model'"
                    )
                    row = cur.fetchone()
                    if row is None:
                        return None
                    val = row["value"]
                    return val if isinstance(val, str) else None
        except Exception as exc:
            logger.warning("Failed to fetch platform model setting: %s", exc)
            return None

    def _fetch_tenant_model_sync(self, tenant_id: str) -> str:
        """Fetch llm_model from tenants.settings."""
        try:
            with psycopg2.connect(self._postgres_url) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute("SET LOCAL app.current_tenant = %s", (tenant_id,))
                    cur.execute(
                        "SELECT settings->>'llm_model' AS llm_model FROM admin.tenants WHERE id = %s",
                        (tenant_id,),
                    )
                    row = cur.fetchone()
                    if row:
                        return row["llm_model"] or self._default_model
        except Exception as exc:
            logger.warning(
                "Failed to fetch tenant model for %s: %s — using default", tenant_id, exc
            )
        return self._default_model

    def _fetch_user_preferences_sync(self, tenant_id: str, user_id: str) -> bool:
        """Fetch thinking_mode from admin.user_preferences for the given user."""
        try:
            with psycopg2.connect(self._postgres_url) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute("SET LOCAL app.current_tenant  = %s", (tenant_id,))
                    cur.execute("SET LOCAL app.current_user_id = %s", (user_id,))
                    cur.execute(
                        """
                        SELECT value
                        FROM admin.user_preferences
                        WHERE user_id = %s AND tenant_id = %s AND key = 'thinking_mode'
                        """,
                        (user_id, tenant_id),
                    )
                    row = cur.fetchone()
                    if row is not None:
                        val = row["value"]
                        if isinstance(val, bool):
                            return val
                        if isinstance(val, str):
                            return val.lower() == "true"
                    return False
        except Exception as exc:
            logger.warning(
                "Failed to fetch user preferences for user=%s tenant=%s: %s — defaulting thinking_mode=False",
                user_id, tenant_id, exc,
            )
            return False
