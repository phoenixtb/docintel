"""
Unit tests for QueryExpander (G2).

litellm.completion is mocked — no live LLM call. These tests cover the pure
expansion logic; the async wiring (timeout, fail-open, union-into-retrieval)
is covered in test_rag_service_stream.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.components.query_transform import QueryExpander


def _mock_completion(content: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    return response


@pytest.mark.unit
class TestQueryExpander:
    def test_disabled_returns_original_unchanged(self):
        expander = QueryExpander(llm_model="test-model", enabled=False)
        result = expander.run(query="remote work policy")
        assert result["expanded_query"] == "remote work policy"
        assert result["search_terms"] == ["remote work policy"]

    def test_expands_query_with_comma_separated_terms(self):
        expander = QueryExpander(llm_model="test-model", enabled=True)
        with patch("litellm.completion", return_value=_mock_completion("WFH, telecommute, work from home")):
            result = expander.run(query="remote work")
        assert result["original_query"] == "remote work"
        assert "WFH" in result["expanded_query"]
        assert "telecommute" in result["expanded_query"]
        assert result["search_terms"] == ["remote work", "WFH", "telecommute", "work from home"]

    def test_fails_open_to_original_on_llm_error(self):
        expander = QueryExpander(llm_model="test-model", enabled=True)
        with patch("litellm.completion", side_effect=RuntimeError("connection refused")):
            result = expander.run(query="termination clause")
        assert result["expanded_query"] == "termination clause"
        assert result["search_terms"] == ["termination clause"]

    def test_strips_whitespace_only_terms(self):
        expander = QueryExpander(llm_model="test-model", enabled=True)
        with patch("litellm.completion", return_value=_mock_completion("term1,  , term2,")):
            result = expander.run(query="q")
        assert result["search_terms"] == ["q", "term1", "term2"]

    def test_model_prefix_applies_openai_prefix_once(self):
        expander = QueryExpander(llm_model="qwen3:1.7b:4bit")
        assert expander.llm_model == "openai/qwen3:1.7b:4bit"

    def test_model_prefix_not_duplicated_when_already_prefixed(self):
        expander = QueryExpander(llm_model="openai/qwen3:1.7b:4bit")
        assert expander.llm_model == "openai/qwen3:1.7b:4bit"

    def test_completion_call_always_passes_a_non_empty_api_key(self):
        """Regression: litellm's OpenAI-compatible client raises 'Missing
        credentials' if api_key is omitted, even against local engines
        (LMForge/Ollama) that ignore its value — must always be set."""
        expander = QueryExpander(llm_model="test-model", enabled=True)
        with patch("litellm.completion", return_value=_mock_completion("term1, term2")) as mock_completion:
            expander.run(query="q")
        assert mock_completion.call_args.kwargs.get("api_key")
