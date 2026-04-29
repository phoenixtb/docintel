"""
LLM Engine Adapter
==================

Thin adapter layer that normalises engine-specific details so the rest of the
codebase stays engine-agnostic.

Two responsibilities:
  1. build_streaming_generator — constructs a ThinkingAwareChatGenerator with the
     correct engine-agnostic parameters (think mode in extra_body, max_tokens cap).
  2. extract_reasoning_content — pulls reasoning content out of a StreamingChunk.

Supported engines (zero code changes, just configure LLM_CHAT_URL):
  LMForge  — think: true/false at top level, delta.reasoning_content in stream
  Ollama   — think: true/false at top level, delta.reasoning_content in stream
  vLLM     — thinking not supported; think goes in extra_body and is ignored
  LM Studio— same as vLLM; extra_body fields are silently ignored

num_ctx is intentionally NOT sent. It is an Ollama-specific per-request extension
(KV-cache size) that LMForge/oMLX/vLLM do not recognise. Context window size is a
model-load-time property in OpenAI-compatible engines. max_tokens is the correct
per-request knob for limiting generation length (incl. reasoning chains).

Haystack 2.x drops reasoning_content from delta before calling the streaming
callback (it only extracts delta.content). ThinkingAwareChatGenerator patches
this by overriding _handle_stream_response to inject reasoning_content into
chunk.meta["reasoning_content"] before the callback is invoked.

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
    extracts delta.content and ignores delta.reasoning_content. We override
    _handle_stream_response to pull reasoning_content from the raw openai-python
    delta and store it in chunk.meta["reasoning_content"] before forwarding the
    chunk to the user's callback.
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
    max_tokens: int,
    temperature: float,
    top_p: Optional[float] = None,
    frequency_penalty: float = 0.0,
    presence_penalty: Optional[float] = None,
    repetition_penalty: Optional[float] = None,
    top_k: Optional[int] = None,
    min_p: Optional[float] = None,
    thinking_budget: Optional[int] = None,
    stream_reasoning_deltas: Optional[bool] = None,
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> ThinkingAwareChatGenerator:
    """
    Return a ThinkingAwareChatGenerator configured for a single streaming request.

    think goes in extra_body — LMForge/Ollama honour it; vLLM/LM Studio ignore it.
    max_tokens is always set (OpenAI spec). Use llm_thinking_max_tokens for thinking
    mode instead of None — an absent max_tokens gives the engine an unlimited budget
    which causes infinite reasoning loops on extended-thinking models.
    temperature and top_p are mode-specific — callers must pass llm_thinking_temperature
    (≥0.6) for thinking mode; near-greedy temps lock Qwen3 into a deterministic
    repetition cycle inside <think> and prevent </think> from ever being emitted.
    num_ctx is intentionally omitted: it is Ollama-specific and not part of the
    OpenAI API. Context window size is a model-load-time property in LMForge/oMLX.

    Extra-body params (LMForge oMLX / Qwen3 spec):
      top_k, min_p — passed in extra_body; silently ignored by vLLM/LM Studio.
      repetition_penalty — passed in extra_body; LMForge oMLX uses it directly
        (wins over derived value); llamacpp/SGLang receive it in extra_body and
        may handle natively.
      thinking_budget — LMForge two-call enforcement; only sent when think=True.
        LMForge strips it before forwarding to the engine so non-LMForge engines
        are unaffected.
      stream_reasoning_deltas — only sent when think=True. Enables LMForge's
        live per-token forwarding of delta.reasoning_content during call 1 so the
        user sees thinking tokens immediately rather than as a bulk dump after
        call 1 completes. Non-LMForge engines ignore unknown extra_body keys.
    presence_penalty is OpenAI-standard and goes into generation_kwargs directly.
    """
    extra: dict = {}
    if think is not None:
        extra["think"] = think
    # thinking_budget and stream_reasoning_deltas only relevant in thinking mode
    if think and thinking_budget is not None:
        extra["thinking_budget"] = thinking_budget
    if think and stream_reasoning_deltas is not None:
        extra["stream_reasoning_deltas"] = stream_reasoning_deltas
    if top_k is not None:
        extra["top_k"] = top_k
    if min_p is not None:
        extra["min_p"] = min_p
    if repetition_penalty is not None:
        extra["repetition_penalty"] = repetition_penalty

    generation_kwargs: dict = {
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if top_p is not None:
        generation_kwargs["top_p"] = top_p
    if frequency_penalty:
        generation_kwargs["frequency_penalty"] = frequency_penalty
    if presence_penalty:
        generation_kwargs["presence_penalty"] = presence_penalty
    if extra:
        generation_kwargs["extra_body"] = extra

    return ThinkingAwareChatGenerator(
        model=model,
        api_base_url=chat_url,
        api_key=Secret.from_token(api_key),
        streaming_callback=streaming_callback,
        generation_kwargs=generation_kwargs,
        timeout=timeout,
        max_retries=max_retries,
    )


def extract_reasoning_content(chunk: StreamingChunk) -> Optional[str]:
    """
    Extract reasoning content from a StreamingChunk.

    ThinkingAwareChatGenerator stores reasoning_content in chunk.meta to avoid
    triggering Haystack's inplace-mutation guard on the typed .reasoning field.
    """
    return chunk.meta.get("reasoning_content")


__all__ = ["build_streaming_generator", "extract_reasoning_content", "ThinkingAwareChatGenerator"]
