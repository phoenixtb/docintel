"""
ModelProfileResolver
====================
Resolves the effective sampling parameters for a given (model_name, tenant_id).

Resolution chain (first non-null value for each param wins):
  1. Tenant DB profile   — exact model_pattern match
  2. Platform DB profile — exact model_pattern match
  3. Tenant DB profile   — best wildcard match (longest pattern)
  4. Platform DB profile — best wildcard match (longest pattern)
  5. Built-in code defaults (BUILTIN_PROFILES, keyed by model family substring)
  6. Caller falls back to global Settings env vars

All fields are resolved independently — a NULL value in a DB profile means
"inherit from next level." Standard: temperature, top_p, max_tokens,
frequency_penalty, presence_penalty, repetition_penalty, top_k, min_p.
Thinking: thinking_temperature, thinking_top_p, thinking_max_tokens,
thinking_frequency_penalty, thinking_presence_penalty, thinking_repetition_penalty,
thinking_top_k, thinking_min_p, thinking_budget, stream_thinking.

`kind` is informational metadata (chat | vlm | embed | rerank) used by the UI
to group/badge profiles and decide which form fields to show. The resolver
itself does NOT filter by kind — pattern matching is unchanged.

Pattern matching:
  - Exact: model_name == pattern
  - Wildcard: pattern ends with "*" → model_name.startswith(pattern[:-1])
  - Longest matching pattern wins when multiple wildcards match.

Both async (`resolve`) and sync (`resolve_sync`) variants are provided so the
resolver can be used from FastAPI endpoints (rag-service) and from synchronous
ThreadPoolExecutor workers (ingestion-service) with a single shared cache.

Results are cached in-process for TTL seconds.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, fields
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_SENTINEL = object()


@dataclass
class ModelSamplingParams:
    """Resolved sampling parameters for one model+mode combination. None = not set."""
    # Standard (non-thinking) params
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None
    top_k: int | None = None
    min_p: float | None = None
    # Thinking-mode params
    thinking_temperature: float | None = None
    thinking_top_p: float | None = None
    thinking_max_tokens: int | None = None
    thinking_frequency_penalty: float | None = None
    thinking_presence_penalty: float | None = None
    thinking_repetition_penalty: float | None = None
    thinking_top_k: int | None = None
    thinking_min_p: float | None = None
    thinking_budget: int | None = None
    stream_thinking: bool | None = None
    # Informational (UI only — never affects resolution)
    kind: str | None = None


def _merge(*params: Optional[ModelSamplingParams]) -> ModelSamplingParams:
    """Merge ordered params left-to-right; first non-None value for each field wins."""
    result = ModelSamplingParams()
    for p in params:
        if p is None:
            continue
        for f in fields(ModelSamplingParams):
            if getattr(result, f.name) is None:
                val = getattr(p, f.name)
                if val is not None:
                    setattr(result, f.name, val)
    return result


# Built-in defaults keyed by model family substring (checked in insertion order).
# These sit below DB profiles in the resolution chain.
BUILTIN_PROFILES: dict[str, ModelSamplingParams] = {
    # Qwen2.5-VL — multimodal vision-language model used for OCR escalation.
    # Default temperature 0.0 produces deterministic but repetition-prone output
    # on dense text pages. Slight stochasticity + frequency/repetition penalties
    # break the loop without sacrificing extraction fidelity.
    # No `thinking_*` — VLMs don't think.
    "qwen2.5-vl": ModelSamplingParams(
        temperature=0.1,
        top_p=0.9,
        max_tokens=4096,
        frequency_penalty=0.3,
        presence_penalty=0.0,
        repetition_penalty=1.15,
    ),
    # Qwen3 official thinking-mode spec:
    #   top_p=0.95, top_k=20, min_p=0, frequency_penalty=0, presence_penalty=0.3
    "qwen3": ModelSamplingParams(
        temperature=0.1,
        thinking_temperature=0.6,
        thinking_top_p=0.95,
        thinking_top_k=20,
        thinking_min_p=0.0,
        thinking_frequency_penalty=0.0,
        thinking_presence_penalty=0.3,
        thinking_repetition_penalty=1.2,
        thinking_max_tokens=6144,
        thinking_budget=4096,
        stream_thinking=True,
    ),
    "qwq": ModelSamplingParams(
        temperature=0.6,
        thinking_temperature=0.7,
        thinking_top_p=0.95,
        thinking_top_k=20,
        thinking_min_p=0.0,
        thinking_frequency_penalty=0.0,
        thinking_presence_penalty=0.3,
        thinking_repetition_penalty=1.2,
        thinking_max_tokens=6144,
        thinking_budget=4096,
        stream_thinking=True,
    ),
    "deepseek-r1": ModelSamplingParams(
        temperature=0.6,
        thinking_temperature=0.7,
        thinking_top_p=0.95,
        thinking_max_tokens=6144,
        stream_thinking=True,
    ),
    "marco-o1": ModelSamplingParams(
        temperature=0.7,
        thinking_temperature=0.7,
        thinking_top_p=0.95,
        thinking_top_k=20,
        thinking_min_p=0.0,
        thinking_frequency_penalty=0.0,
        thinking_presence_penalty=0.3,
        thinking_repetition_penalty=1.2,
        thinking_max_tokens=6144,
        thinking_budget=4096,
        stream_thinking=True,
    ),
    # Catch-all: no built-in overrides — caller uses global Settings env vars
    "*": ModelSamplingParams(),
}


def _builtin_for(model_name: str) -> ModelSamplingParams:
    """Return the built-in profile for the model family, or the catch-all."""
    name_lower = model_name.lower()
    for family, params in BUILTIN_PROFILES.items():
        if family == "*":
            continue
        if family in name_lower:
            return params
    return BUILTIN_PROFILES["*"]


def infer_kind(model_name_or_pattern: str) -> str:
    """
    Infer the `kind` (chat | vlm | embed | rerank) from a model id or pattern.
    Used as the fallback when an explicit `kind` is not stored on a profile row.
    Pure substring match — must agree with the UI's client-side rules.
    """
    s = model_name_or_pattern.lower()
    # Order matters: rerank check first because some embed models include "rerank".
    if "rerank" in s:
        return "rerank"
    if "embed" in s:
        return "embed"
    if "-vl" in s or ":vl" in s or "vision" in s or s.startswith("vl"):
        return "vlm"
    return "chat"


def _match_pattern(model_name: str, pattern: str) -> bool:
    """Return True if pattern matches model_name (exact or prefix wildcard)."""
    if pattern.endswith("*"):
        return model_name.lower().startswith(pattern[:-1].lower())
    return model_name.lower() == pattern.lower()


def _best_match(
    model_name: str, rows: list[dict], target_kind: Optional[str] = None
) -> Optional[ModelSamplingParams]:
    """
    From a list of DB rows, find the best matching profile.

    Priority:
      1. Exact pattern match (kind ignored — exact wins outright).
      2. Wildcard whose `kind` matches `target_kind` (kind-specific wildcard).
      3. Wildcard with NULL kind (universal fallback like `*`).
      4. Wildcard whose `kind` differs from target_kind is excluded so that a
         `kind=chat` wildcard doesn't accidentally tune VLM sampling.

    Within a tier, longest pattern wins.

    `target_kind` is the kind inferred from the model id (chat | vlm | embed |
    rerank). If None, kind filtering is disabled (legacy behavior).
    """
    exact: Optional[ModelSamplingParams] = None
    best_kind_wildcard: Optional[ModelSamplingParams] = None
    best_kind_wildcard_len: int = -1
    best_universal_wildcard: Optional[ModelSamplingParams] = None
    best_universal_wildcard_len: int = -1

    for row in rows:
        pattern = row["model_pattern"]
        if not _match_pattern(model_name, pattern):
            continue
        if not pattern.endswith("*"):
            exact = _row_to_params(row)
            break  # exact match always wins outright

        row_kind = row.get("kind")
        if target_kind is not None and row_kind is not None and row_kind != target_kind:
            # Wrong-kind wildcard — never apply (e.g. chat profile to VLM model).
            continue

        if row_kind == target_kind and target_kind is not None:
            if len(pattern) > best_kind_wildcard_len:
                best_kind_wildcard = _row_to_params(row)
                best_kind_wildcard_len = len(pattern)
        else:
            # Universal (kind IS NULL) or kind filtering disabled.
            if len(pattern) > best_universal_wildcard_len:
                best_universal_wildcard = _row_to_params(row)
                best_universal_wildcard_len = len(pattern)

    return exact or best_kind_wildcard or best_universal_wildcard


def _row_to_params(row: dict) -> ModelSamplingParams:
    return ModelSamplingParams(
        temperature=row.get("temperature"),
        top_p=row.get("top_p"),
        max_tokens=row.get("max_tokens"),
        frequency_penalty=row.get("frequency_penalty"),
        presence_penalty=row.get("presence_penalty"),
        repetition_penalty=row.get("repetition_penalty"),
        top_k=row.get("top_k"),
        min_p=row.get("min_p"),
        thinking_temperature=row.get("thinking_temperature"),
        thinking_top_p=row.get("thinking_top_p"),
        thinking_max_tokens=row.get("thinking_max_tokens"),
        thinking_frequency_penalty=row.get("thinking_frequency_penalty"),
        thinking_presence_penalty=row.get("thinking_presence_penalty"),
        thinking_repetition_penalty=row.get("thinking_repetition_penalty"),
        thinking_top_k=row.get("thinking_top_k"),
        thinking_min_p=row.get("thinking_min_p"),
        thinking_budget=row.get("thinking_budget"),
        stream_thinking=row.get("stream_thinking"),
        kind=row.get("kind"),
    )


class ModelProfileResolver:
    """
    Async-friendly resolver with an in-process TTL cache.

    Cache key: (model_name, tenant_id) → (ModelSamplingParams, expires_at)
    A separate platform-level cache stores all platform profiles so they are
    fetched once and reused across all tenant lookups until TTL expires.

    Both `resolve` (async) and `resolve_sync` (blocking) share the same cache so
    services that mix asyncio with ThreadPoolExecutor workers stay consistent.
    """

    TTL: float = 60.0

    # (model_name, tenant_id) → (ModelSamplingParams, expires_at)
    _cache: dict[tuple[str, str], tuple[object, float]] = {}
    # platform rows cache: (list[dict] | _SENTINEL, expires_at)
    _platform_cache: tuple[object, float] = (_SENTINEL, 0.0)

    def __init__(self, postgres_url: str) -> None:
        self._postgres_url = postgres_url

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def resolve(self, model_name: str, tenant_id: str) -> ModelSamplingParams:
        """Async: return merged sampling params for (model_name, tenant_id)."""
        cached = self._cache_lookup(model_name, tenant_id)
        if cached is not None:
            return cached

        platform_rows, tenant_rows = await asyncio.gather(
            self._get_platform_rows_async(),
            asyncio.get_running_loop().run_in_executor(
                None, self._fetch_tenant_rows_sync, tenant_id
            ),
        )
        return self._build_and_cache(model_name, tenant_id, platform_rows, tenant_rows)

    def resolve_sync(self, model_name: str, tenant_id: str) -> ModelSamplingParams:
        """Sync: return merged sampling params. Safe to call from worker threads."""
        cached = self._cache_lookup(model_name, tenant_id)
        if cached is not None:
            return cached

        platform_rows = self._get_platform_rows_sync()
        tenant_rows = self._fetch_tenant_rows_sync(tenant_id)
        return self._build_and_cache(model_name, tenant_id, platform_rows, tenant_rows)

    def invalidate(self, tenant_id: Optional[str] = None) -> None:
        """
          - tenant_id=None → clear everything (platform profiles changed).
          - tenant_id set  → clear all entries for that tenant + platform cache.
        """
        if tenant_id is None:
            self._cache.clear()
            self.__class__._platform_cache = (_SENTINEL, 0.0)
        else:
            keys_to_remove = [k for k in self._cache if k[1] == tenant_id]
            for k in keys_to_remove:
                del self._cache[k]
            self.__class__._platform_cache = (_SENTINEL, 0.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_lookup(
        self, model_name: str, tenant_id: str
    ) -> Optional[ModelSamplingParams]:
        cached_val, expires_at = self._cache.get((model_name, tenant_id), (_SENTINEL, 0.0))
        if cached_val is not _SENTINEL and time.monotonic() < expires_at:
            return cached_val  # type: ignore[return-value]
        return None

    def _build_and_cache(
        self,
        model_name: str,
        tenant_id: str,
        platform_rows: list[dict],
        tenant_rows: list[dict],
    ) -> ModelSamplingParams:
        # target_kind drives kind-aware wildcard matching: a `kind=vlm` wildcard
        # beats a `kind=null` universal wildcard for VLM models, and `kind=chat`
        # wildcards never apply to VLM/embed/rerank models.
        target_kind = infer_kind(model_name)
        tenant_match = _best_match(model_name, tenant_rows, target_kind)
        platform_match = _best_match(model_name, platform_rows, target_kind)
        builtin = _builtin_for(model_name)
        resolved = _merge(tenant_match, platform_match, builtin)
        self._cache[(model_name, tenant_id)] = (
            resolved,
            time.monotonic() + self.TTL,
        )
        return resolved

    async def _get_platform_rows_async(self) -> list[dict]:
        cached_val, expires_at = self.__class__._platform_cache
        if cached_val is not _SENTINEL and time.monotonic() < expires_at:
            return cached_val  # type: ignore[return-value]

        rows = await asyncio.get_running_loop().run_in_executor(
            None, self._fetch_platform_rows_sync
        )
        self.__class__._platform_cache = (rows, time.monotonic() + self.TTL)
        return rows

    def _get_platform_rows_sync(self) -> list[dict]:
        cached_val, expires_at = self.__class__._platform_cache
        if cached_val is not _SENTINEL and time.monotonic() < expires_at:
            return cached_val  # type: ignore[return-value]
        rows = self._fetch_platform_rows_sync()
        self.__class__._platform_cache = (rows, time.monotonic() + self.TTL)
        return rows

    def _fetch_platform_rows_sync(self) -> list[dict]:
        try:
            with psycopg2.connect(self._postgres_url) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM admin.model_profiles WHERE scope = 'platform'"
                    )
                    return [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            logger.warning("Failed to fetch platform model profiles: %s", exc)
            return []

    def _fetch_tenant_rows_sync(self, tenant_id: str) -> list[dict]:
        try:
            with psycopg2.connect(self._postgres_url) as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                    cur.execute(
                        "SELECT * FROM admin.model_profiles "
                        "WHERE scope = 'tenant' AND tenant_id = %s",
                        (tenant_id,),
                    )
                    return [dict(row) for row in cur.fetchall()]
        except Exception as exc:
            logger.warning(
                "Failed to fetch tenant model profiles for %s: %s", tenant_id, exc
            )
            return []
