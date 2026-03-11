"""
LLM Generation Components
==========================

LiteLLMStreamingGenerator: async token-by-token streaming for SSE endpoints.
OllamaChatGenerator is used for standard (non-streaming) generation via the
Haystack Pipeline — import it directly from haystack_integrations.

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
    """

    def __init__(
        self,
        model: str = "ollama/qwen3.5:4b",
        temperature: float = 0.1,
        max_tokens: int = 1024,
        api_base: str | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_base = api_base or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    async def stream(self, prompt: str):
        """Async generator that yields tokens."""
        response = await litellm.acompletion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            api_base=self.api_base if self.model.startswith("ollama/") else None,
        )

        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content


__all__ = ["LiteLLMStreamingGenerator"]
