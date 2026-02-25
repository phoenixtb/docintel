"""
Centralized Prompts
===================

All prompts used in the RAG service are defined here.
This makes it easy to review, modify, and version prompts.
"""

# =============================================================================
# System Prompts
# =============================================================================

SYSTEM_PROMPT = """You are DocIntel, an intelligent document assistant. You help users find information from their uploaded documents.

Your responsibilities:
- Answer questions accurately based on document context
- Cite sources when referencing specific information
- Be concise but thorough
- If information is not in the documents, say so clearly

Be helpful and professional."""

SYSTEM_PROMPT_WITH_DOMAIN = """You are DocIntel, an intelligent document assistant specializing in {domain} documents.

Your responsibilities:
- Answer questions accurately based on document context
- Cite sources when referencing specific information
- Be concise but thorough
- If information is not in the documents, say so clearly

Be helpful and professional."""

# =============================================================================
# RAG Prompts
# =============================================================================

RAG_PROMPT_TEMPLATE = """Answer the question based on the provided context. If the context doesn't contain enough information to answer, say so clearly.

Context:
{%- for doc in documents %}
---
Source: {{ doc.meta.get('filename', 'Unknown') }} (chunk {{ doc.meta.get('chunk_index', 'N/A') }})
{{ doc.content }}
{%- endfor %}
---

Question: {{ query }}

Answer:"""

RAG_PROMPT_WITH_SOURCES = """Based on the following document excerpts, answer the question. Include source citations in your response.

Documents:
{%- for doc in documents %}
[{{ loop.index }}] {{ doc.meta.get('filename', 'Unknown') }}:
{{ doc.content }}
{% endfor %}

Question: {{ query }}

Provide a clear, concise answer with citations like [1], [2], etc.
Do not repeat information. Do not use bullet lists unless the question asks for a list."""

RAG_PROMPT_WITH_HISTORY = """Based on the following document excerpts, answer the question. Include source citations in your response.

Documents:
{%- for doc in documents %}
[{{ loop.index }}] {{ doc.meta.get('filename', 'Unknown') }}:
{{ doc.content }}
{% endfor %}
{% if history %}
Previous conversation:
{%- for msg in history %}
{{ msg['role'] | capitalize }}: {{ msg['content'] }}
{%- endfor %}
{% endif %}
Question: {{ query }}

Provide a clear, concise answer with citations like [1], [2], etc.
Do not repeat information. Do not use bullet lists unless the question asks for a list."""

# =============================================================================
# No Documents Response
# =============================================================================

NO_DOCUMENTS_RESPONSE = """I don't have any documents to search through yet. 

To get started:
1. Go to the **Documents** page (link in the header)
2. Either upload your own documents or load sample datasets
3. Come back here to ask questions!

Sample datasets include:
- **TechQA**: Technical documentation
- **HR Policies**: Employee policies and procedures  
- **CUAD Contracts**: Legal contract samples"""

NO_RELEVANT_DOCUMENTS_RESPONSE = """I couldn't find relevant information in the uploaded documents to answer your question.

Try:
- Rephrasing your question with different keywords
- Checking if the topic is covered in the uploaded documents
- Uploading additional documents that might contain the answer

Your question: "{query}\""""

# =============================================================================
# Query Expansion Prompt
# =============================================================================

QUERY_EXPANSION_PROMPT = """Given this user question, generate 2-3 alternative phrasings or related search terms that would help find relevant documents. Return only the terms, one per line, without numbering.

Question: {query}

Alternative search terms:"""

# =============================================================================
# Domain Classification Labels
# =============================================================================

DOMAIN_LABELS = ["hr_policy", "technical", "contracts", "general"]

DOMAIN_DESCRIPTIONS = {
    "hr_policy": "Human resources policies, employee handbooks, leave policies, benefits",
    "technical": "Technical documentation, API references, system architecture, code docs",
    "contracts": "Legal contracts, agreements, terms of service, NDAs",
    "general": "General information, company info, miscellaneous documents",
}
