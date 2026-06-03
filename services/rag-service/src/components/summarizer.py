"""
Anchored Iterative Summarizer
==============================

Compresses evicted conversation turns into a rolling summary.
Uses the fast expansion model via an OpenAI-compatible endpoint.

Key design: EXTEND, never replace.
  - Existing summary is preserved as the anchor
  - Only the newly-evicted span is summarized and merged
  - Avoids the "detail drift" problem of full-reconstruction approaches

See: Factory.ai "Evaluating Context Compression for AI Agents" (2025)
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_COMPRESS_PROMPT = """You are a conversation memory assistant. Your job is to maintain a concise rolling summary of a conversation.

Current summary (empty means this is the first compression):
{existing_summary}

New conversation exchanges to incorporate:
{new_exchanges}

Extend the summary to include these new exchanges. Keep the result under 300 words.
Preserve:
- The user's main questions and intent
- Key facts, conclusions, and answers provided
- Documents, topics, or domains referenced
- Any important decisions or findings

Return ONLY the updated summary. No preamble, no explanation."""


def _format_span(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = "User" if m["role"] == "user" else "Assistant"
        lines.append(f"{role}: {m['content']}")
    return "\n\n".join(lines)


class AnchoredSummarizer:
    """
    Anchored iterative summarizer using an OpenAI-compatible endpoint.

    Instantiated once at startup and injected into the query path.
    All calls are async (run_in_executor wraps the sync httpx call).
    Works with any engine (LMForge, Ollama, vLLM, LM Studio) by
    calling the standard POST /v1/chat/completions endpoint.
    """

    def __init__(self, llm_chat_url: str, model: str, api_key: str = "none"):
        self._chat_url = llm_chat_url.rstrip("/")
        self._model = model
        self._api_key = api_key

    def _compress_sync(self, existing_summary: Optional[str], new_span: list[dict]) -> str:
        """Synchronous HTTP call to the chat completions endpoint — runs inside run_in_executor."""
        prompt = _COMPRESS_PROMPT.format(
            existing_summary=existing_summary or "No summary yet.",
            new_exchanges=_format_span(new_span),
        )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        try:
            resp = httpx.post(
                f"{self._chat_url}/chat/completions",
                headers=headers,
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "temperature": 0.1,
                    "max_tokens": 512,
                    # think:false prevents reasoning chains in the summarizer model.
                    # num_ctx is intentionally omitted (Ollama-specific; LMForge/oMLX ignore it).
                    "extra_body": {"think": False},
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            result = resp.json()
            summary = result["choices"][0]["message"]["content"].strip()
            if not summary:
                raise ValueError("Empty response from summarizer model")
            return summary
        except Exception as e:
            logger.warning("Summarizer call failed: %s", e)
            raise

    async def compress(
        self,
        existing_summary: Optional[str],
        new_span: list[dict],
        loop=None,
    ) -> str:
        """Async entry point — offloads sync HTTP to thread pool."""
        import asyncio
        _loop = loop or asyncio.get_running_loop()
        return await _loop.run_in_executor(
            None, lambda: self._compress_sync(existing_summary, new_span)
        )
