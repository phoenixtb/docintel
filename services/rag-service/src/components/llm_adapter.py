"""
LLM Engine Adapter
==================

Thin adapter layer that normalises engine-specific details so the rest of the
codebase stays engine-agnostic.

Two responsibilities:
  1. build_streaming_generator — constructs a ThinkingAwareChatGenerator with the
     correct engine-agnostic parameters (think mode in extra_body, num_ctx passthrough).
  2. extract_reasoning_content — pulls reasoning content out of a StreamingChunk.

Supported engines (zero code changes, just configure LLM_CHAT_URL):
  LMForge  — think: true/false at top level, delta.reasoning_content in stream
  Ollama   — think: true/false at top level, delta.reasoning_content in stream
  vLLM     — thinking not supported; think/num_ctx go in extra_body and are ignored
  LM Studio— same as vLLM; extra_body fields are silently ignored

Haystack 2.x drops reasoning_content from delta before calling the streaming
callback (it only extracts delta.content). ThinkingAwareChatGenerator patches
this by overriding _handle_stream_response to inject reasoning_content into
chunk.reasoning (Haystack 2.18+ typed field) before the callback is invoked.

Private-symbol dependency:
  This module imports _convert_chat_completion_chunk_to_streaming_chunk and
  _convert_streaming_chunks_to_chat_message from haystack's openai.py, and
  overrides the private method _handle_stream_response. These are guarded with
  a defensive ImportError that fails loudly at startup if Haystack renames them.
  pyproject.toml pins haystack-ai~=2.18 so upgrades are deliberate. See T6.
"""

from __future__ import annotations

from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# Defensive import of Haystack private symbols.
# If Haystack renames these between minor releases the error surfaces at
# startup (import time), not mid-stream during a user query.
# ---------------------------------------------------------------------------
try:
    from haystack.components.generators.chat.openai import (
        OpenAIChatGenerator,
        _convert_chat_completion_chunk_to_streaming_chunk,
        _convert_streaming_chunks_to_chat_message,
    )
except ImportError as _e:
    raise RuntimeError(
        "Haystack private symbols moved or removed. llm_adapter.py was last "
        "validated against haystack-ai ~=2.18. Either bump the pin in "
        "pyproject.toml and update the override, or downgrade haystack-ai. "
        f"Original error: {_e}"
    ) from _e

from haystack.dataclasses import ChatMessage, StreamingChunk
from haystack.utils import Secret

# Haystack 2.18+ ships a typed ReasoningContent dataclass for StreamingChunk.reasoning.
# Older versions don't have it; fall back to storing in chunk.meta.
try:
    from haystack.dataclasses import ReasoningContent as _ReasoningContent
    _REASONING_CONTENT_AVAILABLE = True
except ImportError:
    _ReasoningContent = None  # type: ignore[assignment]
    _REASONING_CONTENT_AVAILABLE = False


class ThinkingAwareChatGenerator(OpenAIChatGenerator):
    """
    Subclass of OpenAIChatGenerator that surfaces reasoning_content in the
    StreamingChunk so thinking tokens are not silently dropped.

    Haystack 2.x's _convert_chat_completion_chunk_to_streaming_chunk only
    extracts delta.content and ignores delta.reasoning_content (a non-standard
    field used by LMForge/Ollama for thinking mode). We override
    _handle_stream_response to pull reasoning_content from the raw openai-python
    delta and store it in chunk.reasoning (Haystack 2.18+ typed field) before
    forwarding the chunk to the user's callback.

    Pre-2.18 fallback: stores in chunk.meta["reasoning_content"].
    """

    def _handle_stream_response(self, chat_completion, callback) -> List[ChatMessage]:
        from haystack.dataclasses import ComponentInfo

        component_info = ComponentInfo.from_component(self)
        chunks: List[StreamingChunk] = []

        for raw_chunk in chat_completion:
            chunk_delta = _convert_chat_completion_chunk_to_streaming_chunk(
                chunk=raw_chunk,
                previous_chunks=chunks,
                component_info=component_info,
            )

            # Extract reasoning_content that Haystack discards from the raw delta.
            # Store in meta to avoid triggering Haystack's _warn_on_inplace_mutation,
            # which can cause unexpected behaviour when the typed .reasoning field is
            # set on an already-constructed StreamingChunk (Haystack 2.18+).
            if raw_chunk.choices:
                delta = raw_chunk.choices[0].delta
                rc = getattr(delta, "reasoning_content", None)
                if rc is None and hasattr(delta, "model_extra") and delta.model_extra:
                    rc = delta.model_extra.get("reasoning_content")
                if rc:
                    chunk_delta.meta["reasoning_content"] = rc

            chunks.append(chunk_delta)
            callback(chunk_delta)

        return [_convert_streaming_chunks_to_chat_message(chunks=chunks)]


def build_streaming_generator(
    *,
    model: str,
    chat_url: str,
    api_key: str,
    streaming_callback: Callable[[StreamingChunk], None],
    think: Optional[bool],
    num_ctx: Optional[int],
    max_tokens: Optional[int],
    temperature: float,
    frequency_penalty: float = 0.0,
) -> ThinkingAwareChatGenerator:
    """
    Return a ThinkingAwareChatGenerator configured for a single streaming request.

    Engine-specific params go in extra_body — compatible engines (LMForge, Ollama)
    honour them; others (vLLM, LM Studio) silently ignore unknown fields.

    max_tokens=None omits the field entirely, letting the engine use its default
    unlimited budget — required for thinking mode (thinking tokens share the budget).
    """
    extra: dict = {}
    if think is False:
        # think:false suppresses reasoning_content entirely (for non-thinking mode).
        # think:true is intentionally omitted — LMForge's extended thinking mode
        # with think:true generates only reasoning tokens and never transitions to
        # the answer. Qwen3-family models include reasoning_content naturally even
        # without an explicit think:true flag, so omitting it is the correct way to
        # enable thinking with a bounded budget.
        extra["think"] = False
    if num_ctx is not None:
        extra["num_ctx"] = num_ctx    # Ollama/LMForge: context window; others ignore

    generation_kwargs: dict = {"temperature": temperature}
    if frequency_penalty:
        generation_kwargs["frequency_penalty"] = frequency_penalty
    if max_tokens is not None:
        generation_kwargs["max_tokens"] = max_tokens
    if extra:
        generation_kwargs["extra_body"] = extra

    return ThinkingAwareChatGenerator(
        model=model,
        api_base_url=chat_url,
        api_key=Secret.from_token(api_key),
        streaming_callback=streaming_callback,
        generation_kwargs=generation_kwargs,
    )


def extract_reasoning_content(chunk: StreamingChunk) -> Optional[str]:
    """
    Extract reasoning content from a StreamingChunk.

    ThinkingAwareChatGenerator stores reasoning_content in chunk.meta to avoid
    triggering Haystack's inplace-mutation guard on the typed .reasoning field.
    """
    return chunk.meta.get("reasoning_content")


__all__ = ["build_streaming_generator", "extract_reasoning_content", "ThinkingAwareChatGenerator"]
