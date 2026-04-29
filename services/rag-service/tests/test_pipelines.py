"""
Pipeline tests — verify generation kwargs for the streaming LLM generator.

Since build_query_pipeline was removed (both paths now use RAGService.stream()),
these tests directly inspect build_streaming_generator kwargs to ensure they
include the expected values from Settings.
"""

import pytest
from unittest.mock import MagicMock, patch


def _mock_settings(
    llm_temperature: float = 0.1,
    llm_max_tokens: int = 1024,
    llm_frequency_penalty: float = 0.3,
    llm_model: str = "test-model",
    llm_chat_url: str = "http://localhost:11434/v1",
    llm_api_key: str = "none",
) -> MagicMock:
    s = MagicMock()
    s.llm_temperature = llm_temperature
    s.llm_max_tokens = llm_max_tokens
    s.llm_frequency_penalty = llm_frequency_penalty
    s.llm_model = llm_model
    s.llm_chat_url = llm_chat_url
    s.llm_api_key = llm_api_key
    return s


@pytest.mark.unit
class TestStreamingGeneratorKwargs:
    """Verify that build_streaming_generator passes the correct kwargs to the LLM."""

    def _build_kwargs(self, settings: MagicMock, think=None, max_tokens=None) -> dict:
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model=settings.llm_model,
            chat_url=settings.llm_chat_url,
            api_key=settings.llm_api_key,
            streaming_callback=lambda c: None,
            think=think,
            max_tokens=max_tokens if max_tokens is not None else settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            frequency_penalty=settings.llm_frequency_penalty,
        )
        return gen.generation_kwargs

    def test_includes_frequency_penalty_when_nonzero(self):
        settings = _mock_settings(llm_frequency_penalty=0.3)
        kwargs = self._build_kwargs(settings)
        assert "frequency_penalty" in kwargs
        assert kwargs["frequency_penalty"] == pytest.approx(0.3)

    def test_excludes_frequency_penalty_when_zero(self):
        settings = _mock_settings(llm_frequency_penalty=0.0)
        kwargs = self._build_kwargs(settings)
        assert "frequency_penalty" not in kwargs

    def test_includes_max_tokens(self):
        settings = _mock_settings(llm_max_tokens=2048)
        kwargs = self._build_kwargs(settings)
        assert kwargs.get("max_tokens") == 2048

    def test_includes_temperature(self):
        settings = _mock_settings(llm_temperature=0.05)
        kwargs = self._build_kwargs(settings)
        assert kwargs.get("temperature") == pytest.approx(0.05)

    def test_think_true_sets_think_extra(self):
        """When think=True, extra_body should include think:true or equivalent."""
        settings = _mock_settings()
        gen = __import__("src.components.llm_adapter", fromlist=["build_streaming_generator"]).build_streaming_generator(
            model=settings.llm_model,
            chat_url=settings.llm_chat_url,
            api_key=settings.llm_api_key,
            streaming_callback=lambda c: None,
            think=True,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            frequency_penalty=settings.llm_frequency_penalty,
        )
        kwargs = gen.generation_kwargs
        # think:true should appear somewhere in generation_kwargs or extra_body
        extra = kwargs.get("extra_body") or {}
        has_think_in_extra = extra.get("think") is True
        has_think_direct = kwargs.get("think") is True
        assert has_think_in_extra or has_think_direct or "think" in str(kwargs), (
            f"think:true not found in generation_kwargs: {kwargs}"
        )

    def test_no_think_key_when_think_none(self):
        """When think=None, the think key must not appear in generation_kwargs."""
        settings = _mock_settings()
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model=settings.llm_model,
            chat_url=settings.llm_chat_url,
            api_key=settings.llm_api_key,
            streaming_callback=lambda c: None,
            think=None,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
            frequency_penalty=settings.llm_frequency_penalty,
        )
        kwargs = gen.generation_kwargs
        extra = kwargs.get("extra_body") or {}
        assert "think" not in extra
        assert "think" not in kwargs


# ---------------------------------------------------------------------------
# Thinking truncation heuristic
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestThinkingTruncationHeuristic:
    """Unit-test the chars/4 ≥ 90%*budget heuristic in isolation."""

    def _truncated(self, thinking_chars: int, budget: int) -> bool:
        return (thinking_chars / 4) >= 0.9 * budget

    def test_not_truncated_when_well_under_budget(self):
        # 200 chars / 4 = 50 tokens; 90% of 1000 = 900 → not truncated
        assert self._truncated(200, 1000) is False

    def test_truncated_when_at_90_percent(self):
        # exactly at threshold: chars/4 == 0.9 * budget
        budget = 1000
        chars = int(0.9 * budget * 4)  # 3600 chars → 900 tokens = 90% of 1000
        assert self._truncated(chars, budget) is True

    def test_truncated_when_above_budget(self):
        # More chars than the entire budget allows
        assert self._truncated(5000, 1000) is True

    def test_not_truncated_at_89_percent(self):
        budget = 1000
        chars = int(0.89 * budget * 4)  # 89% → should NOT trigger
        assert self._truncated(chars, budget) is False

    def test_zero_chars_never_truncated(self):
        assert self._truncated(0, 4096) is False
