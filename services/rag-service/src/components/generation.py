"""
LLM Generation Components
==========================

LiteLLMStreamingGenerator: async token-by-token streaming for SSE endpoints.
OpenAIChatGenerator is used for standard (non-streaming) generation via the
Haystack Pipeline — import it directly from haystack.components.generators.openai.

Note: LiteLLMStreamingGenerator is NOT a Haystack @component because it uses
async generator semantics that don't fit Haystack's synchronous Pipeline model.
"""

import logging
import os

import litellm

logger = logging.getLogger(__name__)


class LiteLLMStreamingGenerator:
    """
    Async token streaming over LiteLLM for SSE endpoints.

    Yields string tokens as they arrive from the model.
    Use with asyncio task + queue pattern in the streaming endpoint.

    Uses the generic openai/ LiteLLM prefix so any OpenAI-compatible engine
    (LMForge, Ollama, vLLM, LM Studio) works without code changes.
    """

    def __init__(
        self,
        model: str = "qwen3.5:4b",
        temperature: float = 0.1,
        max_tokens: int = 1024,
        api_base: str | None = None,
    ):
        base_model = model
        # Normalise to openai/ prefix for LiteLLM generic routing
        self.model = f"openai/{base_model}" if not base_model.startswith("openai/") else base_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_base = api_base or os.getenv("LLM_CHAT_URL", "http://host.docker.internal:11434/v1")

    async def stream(self, prompt: str):
        """Async generator that yields tokens."""
        response = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            api_base=self.api_base,
        )

        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content


__all__ = ["LiteLLMStreamingGenerator"]
