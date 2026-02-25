"""
Prompt Builder Component
========================

Builds chat messages for RAG generation from retrieved documents and query.
Outputs list[ChatMessage] so it connects directly to OllamaChatGenerator.messages.

Pipeline position:
  [TransformersSimilarityRanker] → PromptBuilder → OllamaChatGenerator
"""

from haystack import Document, component
from haystack.dataclasses import ChatMessage
from jinja2 import Template
from typing import Optional

from ..prompts import RAG_PROMPT_TEMPLATE, RAG_PROMPT_WITH_HISTORY, RAG_PROMPT_WITH_SOURCES


@component
class PromptBuilder:
    """
    Renders the RAG prompt and wraps it in a ChatMessage for the LLM.

    Uses RAG_PROMPT_WITH_SOURCES by default; RAG_PROMPT_WITH_HISTORY when
    conversation history is provided.
    """

    def __init__(self, template: str | None = None):
        self._template = Template(template or RAG_PROMPT_WITH_SOURCES)
        self._history_template = Template(RAG_PROMPT_WITH_HISTORY)

    @component.output_types(messages=list[ChatMessage])
    def run(
        self,
        documents: list[Document],
        query: str,
        history: Optional[list[dict]] = None,
    ) -> dict:
        if history:
            prompt = self._history_template.render(
                documents=documents, query=query, history=history
            )
        else:
            prompt = self._template.render(documents=documents, query=query)

        return {"messages": [ChatMessage.from_user(prompt)]}


__all__ = ["PromptBuilder"]
