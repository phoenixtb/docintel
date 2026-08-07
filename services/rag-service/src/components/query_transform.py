"""
Query Transformation Components
================================

Vocabulary gap mitigation via LLM-based query expansion.
Future home of HyDE (Hypothetical Document Embeddings).

Pipeline position:
  Query → QueryExpander → Embedder / Retriever
"""

import logging
import os

import litellm
from haystack import component

from ..prompts import QUERY_EXPANSION_PROMPT

logger = logging.getLogger(__name__)


@component
class QueryExpander:
    """
    Expands user query with synonyms and related terms via LLM.

    Addresses vocabulary gap: "WFH" vs "remote work".
    Disabled by default — enable with use_query_expansion=True in settings.

    Uses the generic openai/ LiteLLM prefix so any OpenAI-compatible engine
    (LMForge, Ollama, vLLM, LM Studio) works without code changes.

    Callers (RAGService.stream) are responsible for the hard wall-clock
    timeout — this component makes a single synchronous LLM call with no
    internal retry, so it degrades cleanly when wrapped in asyncio.wait_for.
    """

    def __init__(self, llm_model: str | None = None, enabled: bool = True):
        # Use openai/ prefix — LiteLLM routes to any OpenAI-compatible base URL
        base_model = llm_model or os.getenv("LLM_EXPANSION_MODEL", "qwen3:1.7b:4bit")
        self.llm_model = f"openai/{base_model}" if not base_model.startswith("openai/") else base_model
        self.enabled = enabled
        self.api_base = os.getenv("LLM_CHAT_URL", "http://host.docker.internal:11434/v1")
        # LiteLLM's OpenAI-compatible client requires a non-empty api_key even
        # against local engines that ignore it (LMForge/Ollama/vLLM) — omitting
        # it raises "Missing credentials" before the request is even sent.
        self.api_key = os.getenv("LLM_API_KEY", "none")

    @component.output_types(
        original_query=str,
        expanded_query=str,
        search_terms=list[str],
    )
    def run(self, query: str) -> dict:
        if not self.enabled:
            return {
                "original_query": query,
                "expanded_query": query,
                "search_terms": [query],
            }

        prompt = QUERY_EXPANSION_PROMPT.format(query=query)

        try:
            response = litellm.completion(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=40,
                api_base=self.api_base,
                api_key=self.api_key,
            )

            alternatives = response.choices[0].message.content.strip()
            terms = [t.strip() for t in alternatives.split(",") if t.strip()]

            return {
                "original_query": query,
                "expanded_query": f"{query} {' '.join(terms)}",
                "search_terms": [query] + terms,
            }
        except Exception as e:
            logger.warning("Query expansion failed, using original: %s", e)
            return {
                "original_query": query,
                "expanded_query": query,
                "search_terms": [query],
            }


__all__ = ["QueryExpander"]
