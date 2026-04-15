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

logger = logging.getLogger(__name__)


@component
class QueryExpander:
    """
    Expands user query with synonyms and related terms via LLM.

    Addresses vocabulary gap: "WFH" vs "remote work".
    Disabled by default — enable with use_query_expansion=True in settings.

    Uses the generic openai/ LiteLLM prefix so any OpenAI-compatible engine
    (LMForge, Ollama, vLLM, LM Studio) works without code changes.
    """

    def __init__(self, llm_model: str | None = None, enabled: bool = True):
        # Use openai/ prefix — LiteLLM routes to any OpenAI-compatible base URL
        base_model = llm_model or os.getenv("LLM_EXPANSION_MODEL", "qwen3:1.7b")
        self.llm_model = f"openai/{base_model}" if not base_model.startswith("openai/") else base_model
        self.enabled = enabled
        self.api_base = os.getenv("LLM_CHAT_URL", "http://host.docker.internal:11434/v1")

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

        prompt = (
            f"Given this search query, generate 2-3 alternative phrasings "
            f"or related terms that might appear in documents. Keep it brief.\n\n"
            f"Query: {query}\n\n"
            f"Alternative terms (comma-separated):"
        )

        try:
            response = litellm.completion(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=50,
                api_base=self.api_base,
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
