"""
Prompt Builder Component
========================

Builds chat messages for RAG generation from retrieved documents and query.
Outputs list[ChatMessage] so it connects directly to OllamaChatGenerator.messages.

Uses prompt injection-safe Jinja2 templates with `| e` escaping on all
user-controlled inputs (document content, filenames, query text).
Returns a [system, user] message pair to enforce the system prompt boundary.

Pipeline position:
  [InfinityReranker / OpaChunkValidator] → PromptBuilder → OllamaChatGenerator
"""

from typing import Optional

from haystack import Document, component
from haystack.dataclasses import ChatMessage
from jinja2 import Template

from ..prompts import (
    RAG_PROMPT_INJECTION_SAFE,
    RAG_PROMPT_WITH_HISTORY_SAFE,
    SYSTEM_PROMPT_SECURE,
)


@component
class PromptBuilder:
    """
    Renders injection-safe RAG prompts and returns a [system, user] message pair.

    The system message boundary prevents document content from overriding
    system-level instructions regardless of what the retrieved chunks contain.
    """

    def __init__(self, org_name: str = "your organization"):
        self._org_name = org_name
        self._system_template = Template(SYSTEM_PROMPT_SECURE)
        self._user_template = Template(RAG_PROMPT_INJECTION_SAFE)
        self._user_history_template = Template(RAG_PROMPT_WITH_HISTORY_SAFE)

    @component.output_types(messages=list[ChatMessage])
    def run(
        self,
        documents: list[Document],
        query: str,
        history: Optional[list[dict]] = None,
    ) -> dict:
        system_prompt = self._system_template.render(org_name=self._org_name)

        if history:
            user_prompt = self._user_history_template.render(
                documents=documents, query=query, history=history
            )
        else:
            user_prompt = self._user_template.render(documents=documents, query=query)

        return {
            "messages": [
                ChatMessage.from_system(system_prompt),
                ChatMessage.from_user(user_prompt),
            ]
        }


__all__ = ["PromptBuilder"]
