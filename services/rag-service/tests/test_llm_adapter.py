"""
Unit tests for LLM adapter (ThinkingAwareChatGenerator, extract_reasoning_content,
build_streaming_generator).

All tests run without any running infrastructure — Haystack internals are either
imported normally (unit-testing the actual code path) or mocked at the delta level.
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.unit
class TestThinkingAwareChatGeneratorReasoningInjection:
    """Verify ThinkingAwareChatGenerator injects reasoning_content into StreamingChunk."""

    def _make_fake_chunk(self, content: str | None = None, reasoning_content: str | None = None):
        """Build a fake openai ChatCompletionChunk-like object."""
        delta = MagicMock()
        delta.content = content
        delta.reasoning_content = reasoning_content
        delta.model_extra = {"reasoning_content": reasoning_content} if reasoning_content else {}
        choice = MagicMock()
        choice.delta = delta
        chunk = MagicMock()
        chunk.choices = [choice]
        return chunk

    def test_reasoning_written_to_meta_always(self):
        """reasoning_content is always stored in chunk.meta, never in chunk.reasoning.

        Haystack's _warn_on_inplace_mutation guard fires when .reasoning is set after
        construction. To avoid silent breakage on Haystack upgrades, ThinkingAwareChatGenerator
        always writes to meta['reasoning_content'] regardless of _REASONING_CONTENT_AVAILABLE.
        """
        from src.components.llm_adapter import ThinkingAwareChatGenerator

        received_chunks = []

        def callback(chunk):
            received_chunks.append(chunk)

        fake_raw_chunk = self._make_fake_chunk(content="hello", reasoning_content="I am thinking")

        with (
            patch("src.components.llm_adapter._convert_chat_completion_chunk_to_streaming_chunk") as mock_convert,
            patch("src.components.llm_adapter._convert_streaming_chunks_to_chat_message") as mock_finish,
        ):
            from haystack.dataclasses import StreamingChunk

            fake_sc = StreamingChunk(content="hello")
            mock_convert.return_value = fake_sc
            mock_finish.return_value = MagicMock()

            gen = ThinkingAwareChatGenerator.__new__(ThinkingAwareChatGenerator)
            gen._handle_stream_response([fake_raw_chunk], callback)

        assert len(received_chunks) == 1
        sc = received_chunks[0]
        assert sc.meta.get("reasoning_content") == "I am thinking"
        # .reasoning typed field is intentionally NOT used (avoids Haystack mutation guard)
        assert not getattr(sc, "reasoning", None)

    def test_reasoning_falls_back_to_meta_when_typed_field_unavailable(self):
        """When ReasoningContent is not available, meta['reasoning_content'] is set."""
        from src.components.llm_adapter import ThinkingAwareChatGenerator

        received_chunks = []

        def callback(chunk):
            received_chunks.append(chunk)

        fake_raw_chunk = self._make_fake_chunk(content="hello", reasoning_content="fallback think")

        with (
            patch("src.components.llm_adapter._REASONING_CONTENT_AVAILABLE", False),
            patch("src.components.llm_adapter._convert_chat_completion_chunk_to_streaming_chunk") as mock_convert,
            patch("src.components.llm_adapter._convert_streaming_chunks_to_chat_message") as mock_finish,
        ):
            from haystack.dataclasses import StreamingChunk

            fake_sc = StreamingChunk(content="hello")
            mock_convert.return_value = fake_sc
            mock_finish.return_value = MagicMock()

            gen = ThinkingAwareChatGenerator.__new__(ThinkingAwareChatGenerator)
            gen._handle_stream_response([fake_raw_chunk], callback)

        assert len(received_chunks) == 1
        assert received_chunks[0].meta.get("reasoning_content") == "fallback think"

    def test_no_reasoning_content_produces_no_reasoning_field(self):
        """Chunks without reasoning_content do not set chunk.reasoning."""
        from src.components.llm_adapter import ThinkingAwareChatGenerator

        received_chunks = []

        def callback(chunk):
            received_chunks.append(chunk)

        fake_raw_chunk = self._make_fake_chunk(content="hello", reasoning_content=None)

        with (
            patch("src.components.llm_adapter._convert_chat_completion_chunk_to_streaming_chunk") as mock_convert,
            patch("src.components.llm_adapter._convert_streaming_chunks_to_chat_message") as mock_finish,
        ):
            from haystack.dataclasses import StreamingChunk

            fake_sc = StreamingChunk(content="hello")
            mock_convert.return_value = fake_sc
            mock_finish.return_value = MagicMock()

            gen = ThinkingAwareChatGenerator.__new__(ThinkingAwareChatGenerator)
            gen._handle_stream_response([fake_raw_chunk], callback)

        assert len(received_chunks) == 1
        sc = received_chunks[0]
        assert not sc.meta.get("reasoning_content")
        # reasoning field is either None or absent
        assert not getattr(sc, "reasoning", None)

    def test_reasoning_from_model_extra_when_no_direct_attribute(self):
        """reasoning_content in model_extra is also picked up."""
        from src.components.llm_adapter import ThinkingAwareChatGenerator

        received_chunks = []

        def callback(chunk):
            received_chunks.append(chunk)

        delta = MagicMock()
        delta.content = "answer"
        delta.reasoning_content = None  # direct attr not set
        del delta.reasoning_content  # force AttributeError so getattr returns None
        delta.model_extra = {"reasoning_content": "from_extra"}
        choice = MagicMock()
        choice.delta = delta
        raw_chunk = MagicMock()
        raw_chunk.choices = [choice]

        with (
            patch("src.components.llm_adapter._convert_chat_completion_chunk_to_streaming_chunk") as mock_convert,
            patch("src.components.llm_adapter._convert_streaming_chunks_to_chat_message") as mock_finish,
            patch("src.components.llm_adapter._REASONING_CONTENT_AVAILABLE", False),
        ):
            from haystack.dataclasses import StreamingChunk

            fake_sc = StreamingChunk(content="answer")
            mock_convert.return_value = fake_sc
            mock_finish.return_value = MagicMock()

            gen = ThinkingAwareChatGenerator.__new__(ThinkingAwareChatGenerator)
            gen._handle_stream_response([raw_chunk], callback)

        assert received_chunks[0].meta.get("reasoning_content") == "from_extra"


@pytest.mark.unit
class TestExtractReasoningContent:
    """Tests for extract_reasoning_content helper."""

    def test_meta_field_returns_text(self):
        """extract_reasoning_content reads from meta['reasoning_content'].

        The typed chunk.reasoning field is not used — see ThinkingAwareChatGenerator
        docstring for why we always write to meta.
        """
        from src.components.llm_adapter import extract_reasoning_content
        from haystack.dataclasses import StreamingChunk

        chunk = StreamingChunk(content="hello")
        chunk.meta["reasoning_content"] = "deep thought"

        result = extract_reasoning_content(chunk)
        assert result == "deep thought"

    def test_meta_fallback_returns_text(self):
        """chunk.meta['reasoning_content'] is returned as fallback."""
        from src.components.llm_adapter import extract_reasoning_content
        from haystack.dataclasses import StreamingChunk

        chunk = StreamingChunk(content="hello")
        chunk.meta["reasoning_content"] = "meta fallback"

        with patch("src.components.llm_adapter._REASONING_CONTENT_AVAILABLE", False):
            result = extract_reasoning_content(chunk)

        assert result == "meta fallback"

    def test_no_reasoning_returns_none(self):
        """Returns None when neither field is set."""
        from src.components.llm_adapter import extract_reasoning_content
        from haystack.dataclasses import StreamingChunk

        chunk = StreamingChunk(content="plain answer")
        result = extract_reasoning_content(chunk)
        assert result is None


@pytest.mark.unit
class TestBuildStreamingGenerator:
    """Tests for build_streaming_generator factory."""

    def test_max_tokens_always_present(self):
        """max_tokens is always set in generation_kwargs — never omitted."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            max_tokens=1024,
            temperature=0.1,
        )
        assert gen.generation_kwargs["max_tokens"] == 1024

    def test_max_tokens_thinking_budget(self):
        """Thinking mode uses llm_thinking_max_tokens, not None."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=True,
            max_tokens=8192,
            temperature=0.6,
            top_p=0.95,
        )
        assert gen.generation_kwargs["max_tokens"] == 8192

    def test_top_p_present_when_set(self):
        """top_p is included in generation_kwargs when provided."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=True,
            max_tokens=8192,
            temperature=0.6,
            top_p=0.95,
        )
        assert gen.generation_kwargs["top_p"] == pytest.approx(0.95)

    def test_top_p_absent_when_none(self):
        """top_p=None means the field is omitted (engine uses its default)."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            max_tokens=1024,
            temperature=0.1,
            top_p=None,
        )
        assert "top_p" not in gen.generation_kwargs

    def test_thinking_temperature_higher_than_standard(self):
        """Callers pass a higher temperature for thinking mode — adapter must preserve it."""
        from src.components.llm_adapter import build_streaming_generator

        gen_thinking = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=True,
            max_tokens=4000,
            temperature=0.7,
            top_p=0.8,
        )
        gen_standard = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=False,
            max_tokens=1024,
            temperature=0.2,
        )
        assert gen_thinking.generation_kwargs["temperature"] > gen_standard.generation_kwargs["temperature"]

    def test_frequency_penalty_absent_when_zero(self):
        """frequency_penalty=0.0 is not sent (avoids rejecting engines that don't understand it)."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            max_tokens=1024,
            temperature=0.1,
            frequency_penalty=0.0,
        )
        assert "frequency_penalty" not in gen.generation_kwargs

    def test_frequency_penalty_present_when_nonzero(self):
        """frequency_penalty=0.3 is included in generation_kwargs."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            max_tokens=1024,
            temperature=0.1,
            frequency_penalty=0.3,
        )
        assert gen.generation_kwargs["frequency_penalty"] == pytest.approx(0.3)

    def test_think_true_added_to_extra_body(self):
        """think=True is placed in extra_body; num_ctx is intentionally NOT sent."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=True,
            max_tokens=2000,
            temperature=0.5,
        )
        extra = gen.generation_kwargs.get("extra_body", {})
        assert extra.get("think") is True
        assert "num_ctx" not in extra

    def test_think_none_no_extra_body(self):
        """think=None produces no extra_body (non-thinking engines)."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="some-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            max_tokens=1024,
            temperature=0.1,
        )
        assert "extra_body" not in gen.generation_kwargs

    def test_returns_thinking_aware_generator_instance(self):
        """build_streaming_generator returns a ThinkingAwareChatGenerator."""
        from src.components.llm_adapter import build_streaming_generator, ThinkingAwareChatGenerator

        gen = build_streaming_generator(
            model="test",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            max_tokens=1024,
            temperature=0.1,
        )
        assert isinstance(gen, ThinkingAwareChatGenerator)


@pytest.mark.unit
class TestBuildStreamingGeneratorNewParams:
    """Tests for the new sampling contract parameters (engine-agnostic routing rules).

    These tests are independent of any specific model or backend engine.
    They verify WHERE each parameter ends up (extra_body vs generation_kwargs)
    and WHEN it is omitted, using arbitrary test values.
    """

    def _gen(self, **kwargs):
        from src.components.llm_adapter import build_streaming_generator
        defaults = dict(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=True,
            max_tokens=2000,
            temperature=0.5,
        )
        defaults.update(kwargs)
        return build_streaming_generator(**defaults)

    # ── thinking_budget ────────────────────────────────────────────────────

    def test_thinking_budget_in_extra_body_when_think_true(self):
        gen = self._gen(think=True, thinking_budget=2048)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert extra.get("thinking_budget") == 2048

    def test_thinking_budget_absent_when_think_false(self):
        gen = self._gen(think=False, thinking_budget=2048)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "thinking_budget" not in extra

    def test_thinking_budget_absent_when_think_none(self):
        gen = self._gen(think=None, thinking_budget=2048)
        assert "extra_body" not in gen.generation_kwargs

    def test_thinking_budget_absent_when_none(self):
        gen = self._gen(think=True, thinking_budget=None)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "thinking_budget" not in extra

    # ── stream_reasoning_deltas ───────────────────────────────────────────

    def test_stream_reasoning_deltas_in_extra_body_when_think_true(self):
        gen = self._gen(think=True, stream_reasoning_deltas=True)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert extra.get("stream_reasoning_deltas") is True

    def test_stream_reasoning_deltas_false_also_sent_when_think_true(self):
        gen = self._gen(think=True, stream_reasoning_deltas=False)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert extra.get("stream_reasoning_deltas") is False

    def test_stream_reasoning_deltas_absent_when_think_false(self):
        gen = self._gen(think=False, stream_reasoning_deltas=True)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "stream_reasoning_deltas" not in extra

    def test_stream_reasoning_deltas_absent_when_think_none(self):
        gen = self._gen(think=None, stream_reasoning_deltas=True)
        assert "extra_body" not in gen.generation_kwargs

    def test_stream_reasoning_deltas_absent_when_param_none(self):
        gen = self._gen(think=True, stream_reasoning_deltas=None)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "stream_reasoning_deltas" not in extra

    # ── repetition_penalty ────────────────────────────────────────────────

    def test_repetition_penalty_in_extra_body(self):
        gen = self._gen(repetition_penalty=1.1)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert extra.get("repetition_penalty") == pytest.approx(1.1)

    def test_repetition_penalty_absent_when_none(self):
        """NULL → adapter omits field → oMLX derives from frequency+presence."""
        gen = self._gen(repetition_penalty=None)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "repetition_penalty" not in extra

    # ── top_k / min_p ─────────────────────────────────────────────────────

    def test_top_k_in_extra_body(self):
        gen = self._gen(top_k=20)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert extra.get("top_k") == 20

    def test_top_k_absent_when_none(self):
        gen = self._gen(top_k=None)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "top_k" not in extra

    def test_min_p_zero_in_extra_body(self):
        """min_p=0.0 is an explicit value (not absent) and must be sent.
        0.0 is falsy in Python — the adapter must check `is not None`, not truthiness."""
        gen = self._gen(min_p=0.0)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "min_p" in extra
        assert extra["min_p"] == pytest.approx(0.0)

    def test_min_p_absent_when_none(self):
        gen = self._gen(min_p=None)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "min_p" not in extra

    # ── presence_penalty ──────────────────────────────────────────────────

    def test_presence_penalty_in_generation_kwargs(self):
        """presence_penalty is OpenAI-standard → generation_kwargs, not extra_body."""
        gen = self._gen(presence_penalty=0.3)
        assert gen.generation_kwargs.get("presence_penalty") == pytest.approx(0.3)
        extra = gen.generation_kwargs.get("extra_body", {})
        assert "presence_penalty" not in extra

    def test_presence_penalty_absent_when_none(self):
        gen = self._gen(presence_penalty=None)
        assert "presence_penalty" not in gen.generation_kwargs

    def test_presence_penalty_absent_when_zero(self):
        """0.0 is falsy — not sent to avoid polluting engines that reject unknown kwargs."""
        gen = self._gen(presence_penalty=0.0)
        assert "presence_penalty" not in gen.generation_kwargs

    # ── combined routing contract ─────────────────────────────────────────

    def test_all_params_route_to_correct_destination(self):
        """All new sampling params route to the correct destination.
        Uses arbitrary test values — no model or engine assumptions."""
        gen = self._gen(
            think=True,
            top_p=0.7,
            frequency_penalty=0.0,     # zero → omitted from generation_kwargs
            presence_penalty=0.2,      # non-zero → generation_kwargs
            repetition_penalty=1.05,   # → extra_body
            top_k=10,                  # → extra_body
            min_p=0.0,                 # explicit zero → extra_body (not omitted)
            thinking_budget=512,       # → extra_body only when think=True
        )
        kw = gen.generation_kwargs
        extra = kw.get("extra_body", {})

        # extra_body: engine-extension params
        assert extra.get("think") is True
        assert extra.get("thinking_budget") == 512
        assert extra.get("top_k") == 10
        assert "min_p" in extra and extra["min_p"] == pytest.approx(0.0)
        assert extra.get("repetition_penalty") == pytest.approx(1.05)

        # generation_kwargs: OpenAI-standard params
        assert kw.get("top_p") == pytest.approx(0.7)
        assert kw.get("presence_penalty") == pytest.approx(0.2)

        # frequency_penalty=0.0 must be omitted (falsy → not sent)
        assert "frequency_penalty" not in kw


@pytest.mark.unit
class TestSelfTestThinkingAdapter:
    """Verify the boot-time self-test catches drift in Haystack internals."""

    def test_passes_with_current_haystack(self):
        """_selftest_thinking_adapter does not raise when Haystack is correctly pinned."""
        from haystack.components.generators.chat.openai import OpenAIChatGenerator

        assert hasattr(OpenAIChatGenerator, "_handle_stream_response"), (
            "Haystack pin broken: _handle_stream_response missing. "
            "Update pyproject.toml pin or fix llm_adapter.py."
        )

    def test_private_imports_are_available(self):
        """The two private helper functions can be imported from Haystack."""
        try:
            from haystack.components.generators.chat.openai import (
                _convert_chat_completion_chunk_to_streaming_chunk,
                _convert_streaming_chunks_to_chat_message,
            )
        except ImportError as e:
            pytest.fail(f"Haystack private import failed: {e}")
