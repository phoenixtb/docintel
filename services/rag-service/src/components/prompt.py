"""
Prompt Builder Component
========================

Builds prompts for RAG generation using Jinja2 templates.
"""

from haystack import component, Document
from jinja2 import Template

# Import from centralized prompts
from src.prompts import RAG_PROMPT_TEMPLATE, RAG_PROMPT_WITH_SOURCES, SYSTEM_PROMPT


@component
class PromptBuilder:
    """
    Builds prompts for RAG generation from documents and query.
    Uses Jinja2 templating for flexibility.
    Uses RAG_PROMPT_WITH_SOURCES by default for citation-style answers.
    """

    def __init__(self, template: str | None = None):
        self.template_str = template or RAG_PROMPT_WITH_SOURCES
        self.template = Template(self.template_str)

    @component.output_types(prompt=str)
    def run(self, documents: list[Document], query: str) -> dict:
        prompt = self.template.render(documents=documents, query=query)
        return {"prompt": prompt}


@component
class SystemPromptBuilder:
    """
    Alternative prompt builder with system/user message separation.
    Useful for chat-style LLMs.
    """

    def __init__(self, system_prompt: str | None = None):
        self.system_prompt = system_prompt or SYSTEM_PROMPT

    @component.output_types(messages=list[dict])
    def run(self, documents: list[Document], query: str) -> dict:
        # Build context from documents
        context_parts = []
        for i, doc in enumerate(documents):
            source = doc.meta.get("filename", f"Document {i+1}")
            chunk_idx = doc.meta.get("chunk_index", "N/A")
            context_parts.append(f"[Source: {source}, Chunk: {chunk_idx}]\n{doc.content}")

        context = "\n\n---\n\n".join(context_parts)

        user_message = f"""Based on the following context, answer the question.

Context:
{context}

Question: {query}"""

        return {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_message},
            ]
        }
