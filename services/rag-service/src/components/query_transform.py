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
    """

    def __init__(self, llm_model: str | None = None, enabled: bool = True):
        self.llm_model = llm_model or os.getenv(
            "LITELLM_EXPANSION_MODEL", "ollama/qwen3:1.7b"
        )
        self.enabled = enabled
        self.api_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

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
                api_base=self.api_base if self.llm_model.startswith("ollama/") else None,
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
