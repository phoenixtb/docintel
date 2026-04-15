"""
LLM Engine Adapter
==================

Thin adapter layer that normalises engine-specific details so the rest of the
codebase stays engine-agnostic.

Two responsibilities:
  1. build_streaming_generator — constructs an OpenAIChatGenerator with the
     correct engine-agnostic parameters (think mode in extra_body, num_ctx passthrough).
  2. extract_reasoning_content — pulls reasoning_content out of a StreamingChunk
     regardless of which engine emitted it.

Supported engines (zero code changes, just configure LLM_CHAT_URL):
  LMForge  — think: true/false at top level, delta.reasoning_content in stream
  Ollama   — think: true/false at top level, delta.reasoning_content in stream
  vLLM     — thinks not supported; think/num_ctx go in extra_body and are ignored
  LM Studio— same as vLLM; extra_body fields are silently ignored
"""

from __future__ import annotations

from typing import Callable, Optional

from haystack.components.generators.chat.openai import OpenAIChatGenerator
from haystack.dataclasses import StreamingChunk
from haystack.utils import Secret


def build_streaming_generator(
    *,
    model: str,
    chat_url: str,
    api_key: str,
    streaming_callback: Callable[[StreamingChunk], None],
    think: Optional[bool],
    num_ctx: Optional[int],
    max_tokens: int,
    temperature: float,
) -> OpenAIChatGenerator:
    """
    Return an OpenAIChatGenerator configured for a single streaming request.

    Engine-specific params go in extra_body — compatible engines (LMForge, Ollama)
    honour them; others (vLLM, LM Studio) silently ignore unknown fields.
    """
    extra: dict = {}
    if think is not None:
        extra["think"] = think        # LMForge + Ollama: enable/disable thinking mode
    if num_ctx is not None:
        extra["num_ctx"] = num_ctx    # Ollama/LMForge: context window; others ignore

    generation_kwargs: dict = {
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if extra:
        generation_kwargs["extra_body"] = extra

    return OpenAIChatGenerator(
        model=model,
        api_base_url=chat_url,
        api_key=Secret.from_token(api_key),
        streaming_callback=streaming_callback,
        generation_kwargs=generation_kwargs,
    )


def extract_reasoning_content(chunk: StreamingChunk) -> Optional[str]:
    """
    Extract reasoning_content from a StreamingChunk regardless of engine.

    The openai Python library puts unknown delta fields in model_extra.
    LMForge and Ollama emit reasoning_content as a sibling of content in
    each delta chunk, so it lands in delta.model_extra["reasoning_content"].
    """
    delta = chunk.meta.get("delta")
    if delta is None:
        return None
    # openai-python >= 1.x: unknown response fields go in model_extra
    if hasattr(delta, "model_extra") and delta.model_extra:
        rc = delta.model_extra.get("reasoning_content")
        if rc:
            return rc
    # Direct attribute (if a future openai-python release promotes reasoning_content)
    rc = getattr(delta, "reasoning_content", None)
    if rc:
        return rc
    # Fallback: some Haystack adapter versions flatten it into chunk.meta
    return chunk.meta.get("reasoning_content")


__all__ = ["build_streaming_generator", "extract_reasoning_content"]
