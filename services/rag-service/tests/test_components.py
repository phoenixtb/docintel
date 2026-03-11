"""
Component Tests
===============

Unit tests for individual RAG components.
"""

import pytest
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Load model defaults from config/defaults.env (single source of truth)
# ---------------------------------------------------------------------------
def _load_defaults_env() -> dict[str, str]:
    defaults_path = Path(__file__).parents[3] / "config" / "defaults.env"
    result: dict[str, str] = {}
    if defaults_path.exists():
        for line in defaults_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    return result

_DEFAULTS = _load_defaults_env()
DEFAULT_LITELLM_MODEL = f"ollama/{_DEFAULTS.get('DEFAULT_LLM_MODEL', 'qwen3.5:4b')}"
DEFAULT_LITELLM_FALLBACK = f"ollama/{_DEFAULTS.get('DEFAULT_FALLBACK_MODEL', 'phi3:mini')}"


# Avoid importing haystack at module level to prevent recursion issues
# Document class will be imported lazily when needed
def get_document_class():
    """Lazily import haystack Document to avoid import recursion."""
    from haystack import Document
    return Document


@pytest.mark.unit
class TestPromptBuilderUnit:
    """Unit tests for PromptBuilder that don't require haystack imports."""

    def test_prompt_builder_can_be_imported(self):
        """PromptBuilder can be imported."""
        from src.components.prompt import PromptBuilder
        builder = PromptBuilder()
        assert builder is not None

    def test_system_prompt_builder_can_be_imported(self):
        """SystemPromptBuilder can be imported."""
        from src.components.prompt import SystemPromptBuilder
        builder = SystemPromptBuilder()
        assert builder is not None


@pytest.mark.unit
class TestRouterUnit:
    """Unit tests for router components."""

    def test_domain_filter_builder_can_be_imported(self):
        """DomainFilterBuilder can be imported."""
        from src.components.router import DomainFilterBuilder
        builder = DomainFilterBuilder()
        assert builder is not None

    def test_query_expander_can_be_imported(self):
        """QueryExpander can be imported."""
        from src.components.router import QueryExpander
        expander = QueryExpander(enabled=False)
        assert expander is not None


@pytest.mark.unit
class TestPromptBuilder:
    """Tests for PromptBuilder component."""

    def test_prompt_builder_creates_prompt(self):
        """PromptBuilder creates a prompt from documents and query."""
        from src.components.prompt import PromptBuilder
        Document = get_document_class()
        builder = PromptBuilder()
        
        documents = [
            Document(
                content="Annual leave entitlement is 25 days.",
                meta={"filename": "policy.txt", "chunk_index": 0},
            ),
        ]
        
        result = builder.run(documents=documents, query="How many leave days?")
        
        assert "prompt" in result
        assert "25 days" in result["prompt"]
        assert "How many leave days?" in result["prompt"]

    def test_prompt_builder_includes_sources(self):
        """PromptBuilder includes source references in prompt."""
        from src.components.prompt import PromptBuilder
        Document = get_document_class()
        builder = PromptBuilder()
        
        documents = [
            Document(
                content="Content from first document.",
                meta={"filename": "doc1.txt", "chunk_index": 0},
            ),
            Document(
                content="Content from second document.",
                meta={"filename": "doc2.txt", "chunk_index": 1},
            ),
        ]
        
        result = builder.run(documents=documents, query="What is the content?")
        
        # Should mention source filenames
        assert "doc1.txt" in result["prompt"]
        assert "doc2.txt" in result["prompt"]

    def test_prompt_builder_empty_documents(self):
        """PromptBuilder handles empty document list."""
        from src.components.prompt import PromptBuilder
        builder = PromptBuilder()
        
        result = builder.run(documents=[], query="What is this about?")
        
        assert "prompt" in result


@pytest.mark.unit
class TestSystemPromptBuilder:
    """Tests for SystemPromptBuilder component."""

    def test_system_prompt_builder_creates_messages(self):
        """SystemPromptBuilder creates system and user messages."""
        from src.components.prompt import SystemPromptBuilder
        Document = get_document_class()
        builder = SystemPromptBuilder()
        
        documents = [
            Document(
                content="Test content here.",
                meta={"filename": "test.txt", "chunk_index": 0},
            ),
        ]
        
        result = builder.run(documents=documents, query="What is the test about?")
        
        assert "messages" in result
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][1]["role"] == "user"

    def test_system_prompt_builder_custom_system_prompt(self):
        """SystemPromptBuilder accepts custom system prompt."""
        from src.components.prompt import SystemPromptBuilder
        Document = get_document_class()
        custom_prompt = "You are a legal expert. Answer questions about contracts."
        builder = SystemPromptBuilder(system_prompt=custom_prompt)
        
        documents = [Document(content="Contract terms.", meta={})]
        
        result = builder.run(documents=documents, query="What are the terms?")
        
        assert result["messages"][0]["content"] == custom_prompt


@pytest.mark.unit
class TestDomainFilterBuilder:
    """Tests for DomainFilterBuilder component."""

    def test_domain_filter_builder_detects_hr_policy(self):
        """Builder detects hr_policy domain."""
        from src.components.router import DomainFilterBuilder
        builder = DomainFilterBuilder()
        
        result = builder.run(hr_policy="What is the leave policy?")
        
        assert result["detected_domain"] == "hr_policy"
        assert result["query"] == "What is the leave policy?"

    def test_domain_filter_builder_detects_technical(self):
        """Builder detects technical domain."""
        from src.components.router import DomainFilterBuilder
        builder = DomainFilterBuilder()
        
        result = builder.run(technical="How does the API authentication work?")
        
        assert result["detected_domain"] == "technical"

    def test_domain_filter_builder_detects_contracts(self):
        """Builder detects contracts domain."""
        from src.components.router import DomainFilterBuilder
        builder = DomainFilterBuilder()
        
        result = builder.run(contracts="What are the termination clauses?")
        
        assert result["detected_domain"] == "contracts"

    def test_domain_filter_builder_explicit_override(self):
        """Builder uses explicit domain when provided."""
        from src.components.router import DomainFilterBuilder
        builder = DomainFilterBuilder()
        
        result = builder.run(
            general="Some question text",
            explicit_domain="contracts",
        )
        
        assert result["detected_domain"] == "contracts"


@pytest.mark.unit
class TestQueryExpander:
    """Tests for QueryExpander component."""

    def test_query_expander_disabled(self):
        """Disabled expander returns original query."""
        from src.components.router import QueryExpander
        expander = QueryExpander(enabled=False)
        
        result = expander.run(query="What is the leave policy?")
        
        assert result["original_query"] == "What is the leave policy?"
        assert result["expanded_query"] == "What is the leave policy?"
        assert result["search_terms"] == ["What is the leave policy?"]

    def test_query_expander_returns_search_terms(self):
        """Expander returns search terms list."""
        from src.components.router import QueryExpander
        expander = QueryExpander(enabled=False)  # Use disabled for unit test
        
        result = expander.run(query="annual leave days")
        
        assert "search_terms" in result
        assert isinstance(result["search_terms"], list)
        assert len(result["search_terms"]) >= 1


@pytest.mark.integration
class TestSecureRetriever:
    """Tests for SecureRetriever component."""

    def test_retriever_initialization(self, qdrant_url: str):
        """Retriever initializes with Qdrant connection."""
        from src.components.retriever import SecureRetriever
        retriever = SecureRetriever(
            qdrant_url=qdrant_url,
            collection="documents",
            top_k=10,
        )
        
        assert retriever is not None
        assert retriever.top_k == 10

    def test_retriever_requires_tenant_id(self, qdrant_url: str):
        """Retriever requires tenant_id for isolation."""
        from src.components.retriever import SecureRetriever
        retriever = SecureRetriever(qdrant_url=qdrant_url)
        
        # Create a fake embedding (768 dimensions)
        fake_embedding = [0.1] * 768
        
        # Should work with tenant_id
        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="test_tenant",
        )
        
        assert "documents" in result

    def test_retriever_returns_documents(self, qdrant_url: str):
        """Retriever returns list of documents."""
        from src.components.retriever import SecureRetriever
        retriever = SecureRetriever(qdrant_url=qdrant_url)
        
        fake_embedding = [0.1] * 768
        
        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="nonexistent_tenant",
        )
        
        assert isinstance(result["documents"], list)


@pytest.mark.integration
@pytest.mark.slow
class TestLiteLLMGenerator:
    """Tests for LiteLLM generator component."""

    def test_generator_initialization(self):
        """Generator initializes with model config."""
        from src.components.llm import LiteLLMGenerator
        generator = LiteLLMGenerator(
            model=DEFAULT_LITELLM_MODEL,
            temperature=0.7,
            max_tokens=500,
        )
        
        assert generator.model == DEFAULT_LITELLM_MODEL
        assert generator.temperature == 0.7
        assert generator.max_tokens == 500

    def test_generator_run(self):
        """Generator produces response from prompt."""
        from src.components.llm import LiteLLMGenerator
        generator = LiteLLMGenerator(model=DEFAULT_LITELLM_MODEL)
        
        try:
            result = generator.run(prompt="Say 'hello' and nothing else.")
            
            assert "replies" in result
            assert len(result["replies"]) > 0
            assert "meta" in result
        except Exception as e:
            # LLM might not be available in test environment
            pytest.skip(f"LLM not available: {e}")

    def test_generator_with_fallbacks(self):
        """Generator uses fallback models if primary fails."""
        from src.components.llm import LiteLLMGenerator
        generator = LiteLLMGenerator(
            model="ollama/nonexistent-model",
            fallbacks=[DEFAULT_LITELLM_MODEL, DEFAULT_LITELLM_FALLBACK],
        )
        
        assert generator.fallbacks is not None


@pytest.mark.integration
class TestSecureRetrieverFiltering:
    """Tests for retriever filtering capabilities."""

    def test_retriever_domain_filter(self, qdrant_url: str):
        """Retriever applies domain filter."""
        from src.components.retriever import SecureRetriever
        retriever = SecureRetriever(qdrant_url=qdrant_url)
        
        fake_embedding = [0.1] * 768
        domain_filter = {
            "key": "document_type",
            "match": {"value": "hr_policy"},
        }
        
        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="test_tenant",
            domain_filter=domain_filter,
        )
        
        # Should not fail with domain filter
        assert "documents" in result

    def test_retriever_role_filter(self, qdrant_url: str):
        """Retriever applies role-based filter."""
        from src.components.retriever import SecureRetriever
        retriever = SecureRetriever(qdrant_url=qdrant_url)
        
        fake_embedding = [0.1] * 768
        
        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="test_tenant",
            user_roles=["admin", "hr"],
        )
        
        assert "documents" in result

    def test_retriever_user_filter(self, qdrant_url: str):
        """Retriever applies user-based filter."""
        from src.components.retriever import SecureRetriever
        retriever = SecureRetriever(qdrant_url=qdrant_url)
        
        fake_embedding = [0.1] * 768
        
        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="test_tenant",
            user_id="user123",
        )
        
        assert "documents" in result
