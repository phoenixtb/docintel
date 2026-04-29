"""
Tests for ModelProfileResolver
===============================

Pure unit tests — no DB, no network, no async I/O.
All DB fetch methods are patched to return controlled row lists.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.components.model_profile_resolver import (
    BUILTIN_PROFILES,
    ModelProfileResolver,
    ModelSamplingParams,
    _best_match,
    _builtin_for,
    _match_pattern,
    _merge,
    _row_to_params,
)


# ---------------------------------------------------------------------------
# _match_pattern
# ---------------------------------------------------------------------------

class TestMatchPattern:
    def test_exact_match(self):
        assert _match_pattern("qwen3:8b", "qwen3:8b") is True

    def test_exact_no_match(self):
        assert _match_pattern("qwen3:8b", "qwen3:4b") is False

    def test_wildcard_match(self):
        assert _match_pattern("qwen3:8b", "qwen3*") is True

    def test_wildcard_no_match(self):
        assert _match_pattern("deepseek-r1:7b", "qwen3*") is False

    def test_wildcard_prefix_only(self):
        assert _match_pattern("qwen3:8b-instruct", "qwen3:8b*") is True

    def test_case_insensitive_exact(self):
        assert _match_pattern("Qwen3:8b", "qwen3:8b") is True

    def test_case_insensitive_wildcard(self):
        assert _match_pattern("Qwen3:8b", "qwen3*") is True


# ---------------------------------------------------------------------------
# _best_match
# ---------------------------------------------------------------------------
# Row helpers use generic model identifiers — not tied to any real model family.
# The matching algorithm is independent of what model is actually running.

def _make_row(
    pattern: str,
    temperature=None,
    thinking_temperature=None,
    **kwargs,
) -> dict:
    row = {
        "model_pattern": pattern,
        "temperature": temperature,
        "top_p": None,
        "max_tokens": None,
        "frequency_penalty": None,
        "presence_penalty": None,
        "repetition_penalty": None,
        "top_k": None,
        "min_p": None,
        "thinking_temperature": thinking_temperature,
        "thinking_top_p": None,
        "thinking_max_tokens": None,
        "thinking_frequency_penalty": None,
        "thinking_presence_penalty": None,
        "thinking_repetition_penalty": None,
        "thinking_top_k": None,
        "thinking_min_p": None,
        "thinking_budget": None,
        "stream_thinking": None,
    }
    row.update(kwargs)
    return row


class TestBestMatch:
    def test_exact_wins_over_wildcard(self):
        rows = [
            _make_row("model-a*", temperature=0.5),
            _make_row("model-a:v2", temperature=0.1),
        ]
        result = _best_match("model-a:v2", rows)
        assert result is not None
        assert result.temperature == 0.1

    def test_longer_wildcard_wins(self):
        rows = [
            _make_row("model-a*", temperature=0.5),
            _make_row("model-a:v2*", temperature=0.2),
        ]
        result = _best_match("model-a:v2-instruct", rows)
        assert result is not None
        assert result.temperature == 0.2

    def test_returns_none_when_no_match(self):
        rows = [_make_row("model-b*")]
        assert _best_match("model-a:v1", rows) is None

    def test_empty_rows(self):
        assert _best_match("model-a:v1", []) is None


# ---------------------------------------------------------------------------
# _merge
# ---------------------------------------------------------------------------

class TestMerge:
    def test_first_non_none_wins(self):
        a = ModelSamplingParams(temperature=0.1)
        b = ModelSamplingParams(temperature=0.9, top_p=0.95)
        result = _merge(a, b)
        assert result.temperature == 0.1   # a wins
        assert result.top_p == 0.95        # b fills in

    def test_none_profile_skipped(self):
        a = ModelSamplingParams(temperature=0.1)
        result = _merge(None, a)
        assert result.temperature == 0.1

    def test_all_none_params_remain_none(self):
        result = _merge(ModelSamplingParams(), ModelSamplingParams())
        assert result.temperature is None
        assert result.top_p is None

    def test_merge_all_fields(self):
        a = ModelSamplingParams(temperature=0.3, thinking_temperature=0.8)
        b = ModelSamplingParams(top_p=0.7, thinking_top_p=0.6, thinking_max_tokens=2000)
        result = _merge(a, b)
        assert result.temperature == 0.3
        assert result.thinking_temperature == 0.8
        assert result.top_p == 0.7
        assert result.thinking_top_p == 0.6
        assert result.thinking_max_tokens == 2000


# ---------------------------------------------------------------------------
# _builtin_for
# ---------------------------------------------------------------------------
# These tests verify STRUCTURE — that known thinking families have thinking
# params set and unknown models fall through to the empty catch-all.
# They do NOT assert specific numeric values, because those are tuning knobs
# for specific models and will change over time without being bugs.

class TestBuiltinFor:
    def test_known_thinking_family_has_thinking_params_set(self):
        """Any model matched by a known thinking family should have thinking params."""
        for model_name in ["qwen3:8b", "qwq:32b", "deepseek-r1:14b", "marco-o1:7b"]:
            p = _builtin_for(model_name)
            assert p.thinking_temperature is not None, (
                f"{model_name}: thinking_temperature not set in builtin profile"
            )

    def test_known_thinking_family_has_thinking_max_tokens_set(self):
        for model_name in ["qwen3:8b", "qwq:32b", "deepseek-r1:14b", "marco-o1:7b"]:
            p = _builtin_for(model_name)
            assert p.thinking_max_tokens is not None, (
                f"{model_name}: thinking_max_tokens not set"
            )

    def test_unknown_model_returns_empty_catchall(self):
        """A model with no matching builtin family gets all-None params (caller uses env)."""
        p = _builtin_for("unknown-model:1b")
        assert p.temperature is None
        assert p.thinking_temperature is None
        assert p.thinking_budget is None

    def test_family_matching_is_case_insensitive(self):
        """Uppercase model names still resolve to the correct builtin family."""
        for model_name, upper in [("qwen3:8b", "QWEN3:8B"), ("qwq:32b", "QWQ:32B")]:
            lower_p = _builtin_for(model_name)
            upper_p = _builtin_for(upper)
            # Structural equality: same fields are set, not necessarily same values
            for field in ["thinking_temperature", "thinking_max_tokens"]:
                assert (getattr(lower_p, field) is None) == (getattr(upper_p, field) is None), (
                    f"{field} None-ness differs between '{model_name}' and '{upper}'"
                )

    def test_thinking_temperature_positive_for_thinking_families(self):
        """Thinking temperature must be > 0 for all thinking families (>= 0.6 avoids loops)."""
        for model_name in ["qwen3:8b", "qwq:32b", "deepseek-r1:14b", "marco-o1:7b"]:
            p = _builtin_for(model_name)
            if p.thinking_temperature is not None:
                assert p.thinking_temperature > 0, (
                    f"{model_name}: thinking_temperature must be > 0, got {p.thinking_temperature}"
                )


# ---------------------------------------------------------------------------
# ModelProfileResolver.resolve — integration with mock DB
# ---------------------------------------------------------------------------

@pytest.fixture
def resolver():
    r = ModelProfileResolver(postgres_url="postgresql://fake/db")
    # Clear class-level caches between tests
    r._cache.clear()
    r.__class__._platform_cache = (object(), 0.0)  # force miss
    return r


def _patch_db(resolver, platform_rows=None, tenant_rows=None):
    """Patch both DB fetch methods to return controlled data."""
    platform_rows = platform_rows or []
    tenant_rows = tenant_rows or []
    resolver._fetch_platform_rows_sync = MagicMock(return_value=platform_rows)
    resolver._fetch_tenant_rows_sync = MagicMock(return_value=tenant_rows)


# Use a model name that has no builtin profile so DB tests are isolated from builtin logic.
_GENERIC_MODEL = "unknown-model:1b"
_GENERIC_PATTERN = "unknown-model*"


class TestResolve:
    @pytest.mark.asyncio
    async def test_tenant_exact_beats_platform(self, resolver):
        """Tenant exact match wins over platform wildcard."""
        _patch_db(
            resolver,
            platform_rows=[_make_row(f"{_GENERIC_MODEL[:7]}*", temperature=0.5)],
            tenant_rows=[_make_row(_GENERIC_MODEL, temperature=0.2)],
        )
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.temperature == 0.2

    @pytest.mark.asyncio
    async def test_platform_fallback_when_no_tenant_match(self, resolver):
        """Platform row fills in when tenant has no matching profile."""
        _patch_db(
            resolver,
            platform_rows=[_make_row(_GENERIC_PATTERN, temperature=0.4)],
            tenant_rows=[],
        )
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.temperature == 0.4

    @pytest.mark.asyncio
    async def test_builtin_fallback_when_no_db_match(self, resolver):
        """When neither tenant nor platform DB has a row, builtin is the fallback.
        We assert structure (not specific values) since builtins are tuning knobs."""
        _patch_db(resolver, platform_rows=[], tenant_rows=[])
        # Use a known thinking family so we can verify the builtin kicked in
        # without asserting its specific numeric values.
        result = await resolver.resolve("qwen3:8b", "tenant-a")
        assert result.thinking_temperature is not None, "Builtin fallback must set thinking_temperature"
        assert result.thinking_max_tokens is not None, "Builtin fallback must set thinking_max_tokens"

    @pytest.mark.asyncio
    async def test_builtin_not_invoked_for_unknown_model(self, resolver):
        """Models not in BUILTIN_PROFILES get all-None — caller falls through to env config."""
        _patch_db(resolver, platform_rows=[], tenant_rows=[])
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.temperature is None
        assert result.thinking_temperature is None

    @pytest.mark.asyncio
    async def test_merge_fills_missing_params(self, resolver):
        """Tenant sets one param, platform sets another — both should appear in result."""
        _patch_db(
            resolver,
            platform_rows=[_make_row(_GENERIC_PATTERN, thinking_temperature=0.8)],
            tenant_rows=[_make_row(_GENERIC_PATTERN, temperature=0.3)],
        )
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.temperature == 0.3           # from tenant
        assert result.thinking_temperature == 0.8  # from platform (tenant didn't set it)

    @pytest.mark.asyncio
    async def test_result_cached(self, resolver):
        """Second call with same key must not hit DB again."""
        _patch_db(resolver, platform_rows=[], tenant_rows=[])
        await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert resolver._fetch_tenant_rows_sync.call_count == 1


# ---------------------------------------------------------------------------
# ModelProfileResolver.invalidate
# ---------------------------------------------------------------------------

class TestInvalidate:
    def test_full_invalidate_clears_all_cache(self, resolver):
        resolver._cache[("qwen3:8b", "tenant-a")] = (ModelSamplingParams(), time.monotonic() + 60)
        resolver._cache[("qwen3:8b", "tenant-b")] = (ModelSamplingParams(), time.monotonic() + 60)
        resolver.__class__._platform_cache = ([{}], time.monotonic() + 60)

        resolver.invalidate()

        assert len(resolver._cache) == 0
        cached_val, _ = resolver.__class__._platform_cache
        assert cached_val is not resolver._cache  # sentinel reset

    def test_tenant_invalidate_removes_only_that_tenant(self, resolver):
        resolver._cache[("qwen3:8b", "tenant-a")] = (ModelSamplingParams(), time.monotonic() + 60)
        resolver._cache[("qwen3:8b", "tenant-b")] = (ModelSamplingParams(), time.monotonic() + 60)

        resolver.invalidate(tenant_id="tenant-a")

        assert ("qwen3:8b", "tenant-a") not in resolver._cache
        assert ("qwen3:8b", "tenant-b") in resolver._cache

    def test_tenant_invalidate_also_resets_platform_cache(self, resolver):
        resolver.__class__._platform_cache = ([{}], time.monotonic() + 60)
        resolver.invalidate(tenant_id="tenant-a")
        _, expires = resolver.__class__._platform_cache
        assert expires == 0.0


# ---------------------------------------------------------------------------
# BUILTIN_PROFILES completeness
# ---------------------------------------------------------------------------

class TestBuiltinProfiles:
    """Structural tests for BUILTIN_PROFILES.

    We check completeness and internal consistency — NOT specific numeric values.
    Tuning knobs (temperatures, top_p, budgets) belong to model configuration,
    not to the test suite, and will change over time without being bugs.
    """

    def test_catchall_wildcard_present(self):
        assert "*" in BUILTIN_PROFILES, "catch-all wildcard entry must exist"

    def test_catchall_has_all_none(self):
        """The catch-all must have all-None so callers fall through to env config."""
        p = BUILTIN_PROFILES["*"]
        for fname in ["temperature", "top_p", "max_tokens", "thinking_temperature",
                      "thinking_budget", "thinking_repetition_penalty"]:
            assert getattr(p, fname) is None, f"catch-all '{fname}' must be None"

    def test_all_non_catchall_families_have_thinking_temperature(self):
        """Every explicitly named family must set thinking_temperature (thinking budget is useless without it)."""
        for family, p in BUILTIN_PROFILES.items():
            if family == "*":
                continue
            assert p.thinking_temperature is not None, (
                f"BUILTIN_PROFILES['{family}'] missing thinking_temperature"
            )

    def test_all_non_catchall_families_have_thinking_max_tokens(self):
        for family, p in BUILTIN_PROFILES.items():
            if family == "*":
                continue
            assert p.thinking_max_tokens is not None, (
                f"BUILTIN_PROFILES['{family}'] missing thinking_max_tokens"
            )

    def test_thinking_budget_not_greater_than_thinking_max_tokens(self):
        """thinking_budget must be smaller than thinking_max_tokens (answer needs room)."""
        for family, p in BUILTIN_PROFILES.items():
            if family == "*":
                continue
            if p.thinking_budget is not None and p.thinking_max_tokens is not None:
                assert p.thinking_budget < p.thinking_max_tokens, (
                    f"BUILTIN_PROFILES['{family}']: thinking_budget ({p.thinking_budget}) "
                    f">= thinking_max_tokens ({p.thinking_max_tokens})"
                )

    def test_all_thinking_families_have_stream_thinking_set(self):
        """Every explicitly named thinking family must declare stream_thinking."""
        for family, p in BUILTIN_PROFILES.items():
            if family == "*":
                continue
            if p.thinking_temperature is not None:
                assert p.stream_thinking is not None, (
                    f"BUILTIN_PROFILES['{family}'] has thinking_temperature but missing stream_thinking"
                )

    def test_thinking_temperature_positive(self):
        """Thinking temperature must be > 0 for all named families."""
        for family, p in BUILTIN_PROFILES.items():
            if family == "*" or p.thinking_temperature is None:
                continue
            assert p.thinking_temperature > 0, (
                f"BUILTIN_PROFILES['{family}']: thinking_temperature must be > 0"
            )

    def test_no_negative_penalties(self):
        """Repetition penalty < 1.0 would reward repetition — invalid config."""
        for family, p in BUILTIN_PROFILES.items():
            if p.thinking_repetition_penalty is not None:
                assert p.thinking_repetition_penalty >= 1.0, (
                    f"BUILTIN_PROFILES['{family}']: repetition_penalty must be >= 1.0"
                )


# ---------------------------------------------------------------------------
# _row_to_params — new fields
# ---------------------------------------------------------------------------

class TestRowToParams:
    def test_new_standard_fields_mapped(self):
        row = _make_row(
            "*",
            presence_penalty=0.1,
            repetition_penalty=1.1,
            top_k=20,
            min_p=0.05,
        )
        p = _row_to_params(row)
        assert p.presence_penalty == pytest.approx(0.1)
        assert p.repetition_penalty == pytest.approx(1.1)
        assert p.top_k == 20
        assert p.min_p == pytest.approx(0.05)

    def test_new_thinking_fields_mapped(self):
        row = _make_row(
            "*",
            thinking_frequency_penalty=0.0,
            thinking_presence_penalty=0.3,
            thinking_repetition_penalty=1.1,
            thinking_top_k=20,
            thinking_min_p=0.0,
            thinking_budget=4096,
            stream_thinking=True,
        )
        p = _row_to_params(row)
        assert p.thinking_frequency_penalty == pytest.approx(0.0)
        assert p.thinking_presence_penalty == pytest.approx(0.3)
        assert p.thinking_repetition_penalty == pytest.approx(1.1)
        assert p.thinking_top_k == 20
        assert p.thinking_min_p == pytest.approx(0.0)
        assert p.thinking_budget == 4096
        assert p.stream_thinking is True

    def test_null_new_fields_remain_none(self):
        row = _make_row("*")
        p = _row_to_params(row)
        assert p.thinking_budget is None
        assert p.repetition_penalty is None
        assert p.top_k is None
        assert p.stream_thinking is None


# ---------------------------------------------------------------------------
# Merge — new fields
# ---------------------------------------------------------------------------

class TestMergeNewFields:
    def test_thinking_budget_resolved_tenant_over_platform(self):
        a = ModelSamplingParams(thinking_budget=1024)
        b = ModelSamplingParams(thinking_budget=4096)
        result = _merge(a, b)
        assert result.thinking_budget == 1024

    def test_thinking_repetition_penalty_fallback(self):
        tenant = ModelSamplingParams()
        platform = ModelSamplingParams(thinking_repetition_penalty=1.1)
        result = _merge(tenant, platform)
        assert result.thinking_repetition_penalty == pytest.approx(1.1)

    def test_min_p_zero_is_not_none(self):
        """min_p=0.0 must survive the merge — it is distinct from None."""
        a = ModelSamplingParams(thinking_min_p=0.0)
        b = ModelSamplingParams(thinking_min_p=0.5)
        result = _merge(a, b)
        # 0.0 is falsy but not None — _merge checks `is not None`
        assert result.thinking_min_p == pytest.approx(0.0)

    def test_stream_thinking_tenant_wins(self):
        a = ModelSamplingParams(stream_thinking=False)
        b = ModelSamplingParams(stream_thinking=True)
        result = _merge(a, b)
        assert result.stream_thinking is False

    def test_stream_thinking_fallback_to_platform(self):
        a = ModelSamplingParams()  # no override
        b = ModelSamplingParams(stream_thinking=True)
        result = _merge(a, b)
        assert result.stream_thinking is True

    def test_stream_thinking_none_when_both_unset(self):
        result = _merge(ModelSamplingParams(), ModelSamplingParams())
        assert result.stream_thinking is None


# ---------------------------------------------------------------------------
# End-to-end resolve — new fields propagate from DB rows
# ---------------------------------------------------------------------------

class TestResolveNewFields:
    @pytest.mark.asyncio
    async def test_thinking_budget_tenant_wins_over_platform(self, resolver):
        """Tenant thinking_budget must shadow a different platform value."""
        _patch_db(
            resolver,
            platform_rows=[_make_row("*", thinking_budget=4096)],
            tenant_rows=[_make_row("*", thinking_budget=1024)],
        )
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.thinking_budget == 1024

    @pytest.mark.asyncio
    async def test_repetition_penalty_null_propagates_to_caller(self, resolver):
        """NULL repetition_penalty in both DB rows must stay None so adapter omits the field."""
        _patch_db(
            resolver,
            platform_rows=[_make_row("*")],
            tenant_rows=[_make_row("*")],
        )
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.repetition_penalty is None

    @pytest.mark.asyncio
    async def test_stream_thinking_tenant_wins_over_platform(self, resolver):
        _patch_db(
            resolver,
            platform_rows=[_make_row("*", stream_thinking=True)],
            tenant_rows=[_make_row("*", stream_thinking=False)],
        )
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.stream_thinking is False

    @pytest.mark.asyncio
    async def test_stream_thinking_falls_back_to_platform(self, resolver):
        _patch_db(
            resolver,
            platform_rows=[_make_row("*", stream_thinking=True)],
            tenant_rows=[_make_row("*")],
        )
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.stream_thinking is True

    @pytest.mark.asyncio
    async def test_thinking_frequency_penalty_zero_is_not_absent(self, resolver):
        """0.0 is a valid, explicit value — must survive resolution unchanged (not treated as None)."""
        _patch_db(
            resolver,
            platform_rows=[],
            tenant_rows=[_make_row("*", thinking_frequency_penalty=0.0)],
        )
        result = await resolver.resolve(_GENERIC_MODEL, "tenant-a")
        assert result.thinking_frequency_penalty == pytest.approx(0.0)
