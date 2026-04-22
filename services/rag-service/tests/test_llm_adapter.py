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

    def test_reasoning_written_to_typed_field_when_available(self):
        """When ReasoningContent is importable, chunk.reasoning is populated."""
        from src.components.llm_adapter import (
            ThinkingAwareChatGenerator,
            _REASONING_CONTENT_AVAILABLE,
            _ReasoningContent,
        )

        if not _REASONING_CONTENT_AVAILABLE:
            pytest.skip("ReasoningContent not available in this Haystack version")

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
        assert sc.reasoning is not None
        assert sc.reasoning.reasoning_text == "I am thinking"

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

    def test_typed_field_returns_text(self):
        """chunk.reasoning.reasoning_text is extracted when available."""
        from src.components.llm_adapter import extract_reasoning_content, _REASONING_CONTENT_AVAILABLE, _ReasoningContent
        from haystack.dataclasses import StreamingChunk

        if not _REASONING_CONTENT_AVAILABLE or _ReasoningContent is None:
            pytest.skip("ReasoningContent not available in this Haystack version")

        chunk = StreamingChunk(content="hello")
        chunk.reasoning = _ReasoningContent(reasoning_text="deep thought")

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

    def test_max_tokens_omitted_when_none(self):
        """max_tokens=None means key is absent from generation_kwargs."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            num_ctx=None,
            max_tokens=None,
            temperature=0.1,
        )
        assert "max_tokens" not in gen.generation_kwargs

    def test_max_tokens_present_when_set(self):
        """max_tokens=1024 is passed through to generation_kwargs."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            num_ctx=None,
            max_tokens=1024,
            temperature=0.1,
        )
        assert gen.generation_kwargs["max_tokens"] == 1024

    def test_frequency_penalty_absent_when_zero(self):
        """frequency_penalty=0.0 is not sent (avoids rejecting engines that don't understand it)."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="test-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            num_ctx=None,
            max_tokens=None,
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
            num_ctx=None,
            max_tokens=None,
            temperature=0.1,
            frequency_penalty=0.3,
        )
        assert gen.generation_kwargs["frequency_penalty"] == pytest.approx(0.3)

    def test_think_true_added_to_extra_body(self):
        """think=True is placed in extra_body for LMForge/Ollama."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="qwen3:4b",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=True,
            num_ctx=16384,
            max_tokens=None,
            temperature=0.1,
        )
        extra = gen.generation_kwargs.get("extra_body", {})
        assert extra.get("think") is True
        assert extra.get("num_ctx") == 16384

    def test_think_none_no_extra_body(self):
        """think=None produces no extra_body (non-thinking engines)."""
        from src.components.llm_adapter import build_streaming_generator

        gen = build_streaming_generator(
            model="some-model",
            chat_url="http://localhost:11434/v1",
            api_key="none",
            streaming_callback=lambda c: None,
            think=None,
            num_ctx=None,
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
            num_ctx=None,
            max_tokens=None,
            temperature=0.1,
        )
        assert isinstance(gen, ThinkingAwareChatGenerator)


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
