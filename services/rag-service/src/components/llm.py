"""
LiteLLM Generator Component
===========================

Haystack component wrapping LiteLLM for provider flexibility.
Same code works with Ollama (local) or cloud APIs.
"""

from haystack import component
import litellm
import os


@component
class LiteLLMGenerator:
    """
    Haystack component wrapping LiteLLM for provider flexibility.
    Same code works with Ollama (local) or cloud APIs.
    """

    def __init__(
        self,
        model: str = "ollama/qwen3:4b",
        fallbacks: list[str] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        api_base: str | None = None,
    ):
        self.model = model
        self.fallbacks = fallbacks or []
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.api_base = api_base or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

        # Set Ollama base URL for litellm
        if model.startswith("ollama/"):
            litellm.api_base = self.api_base

    @component.output_types(replies=list[str], meta=dict)
    def run(self, prompt: str) -> dict:
        response = litellm.completion(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            fallbacks=self.fallbacks if self.fallbacks else None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            api_base=self.api_base if self.model.startswith("ollama/") else None,
        )
        return {
            "replies": [response.choices[0].message.content],
            "meta": {
                "model": response.model,
                "usage": dict(response.usage) if response.usage else {},
            },
        }


class LiteLLMStreamingGenerator:
    """
    Streaming version of LiteLLM generator for SSE responses.
    Yields tokens as they are generated.
    
    Note: This is NOT a Haystack component because it uses async streaming.
    Use directly for SSE endpoints.
    """

    def __init__(
        self,
        model: str = "ollama/qwen3:4b",
        fallbacks: list[str] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1024,
        api_base: str | None = None,
    ):
        self.model = model
        self.fallbacks = fallbacks or []
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
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
