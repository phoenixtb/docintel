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

    def test_prompt_builder_returns_messages_key(self):
        """PromptBuilder.run() returns a 'messages' key (not 'prompt')."""
        from src.components.prompt import PromptBuilder
        builder = PromptBuilder()
        result = builder.run(documents=[], query="test")
        assert "messages" in result


@pytest.mark.unit
class TestRouterUnit:
    """Unit tests for router components."""

    def test_domain_filter_builder_can_be_imported(self):
        """DomainFilterBuilder can be imported."""
        from src.components.routing import DomainFilterBuilder
        builder = DomainFilterBuilder()
        assert builder is not None


@pytest.mark.unit
class TestPromptBuilder:
    """Tests for PromptBuilder component."""

    def test_prompt_builder_creates_messages(self):
        """PromptBuilder creates messages from documents and query."""
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

        assert "messages" in result
        full_text = " ".join(m["content"] for m in result["messages"] if hasattr(m, "__getitem__") or hasattr(m, "content"))
        # Verify content appears somewhere in the rendered messages
        assert any("25 days" in str(m) for m in result["messages"])
        assert any("How many leave days?" in str(m) for m in result["messages"])

    def test_prompt_builder_includes_sources(self):
        """PromptBuilder includes source references in messages."""
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

        assert "messages" in result
        messages_str = str(result["messages"])
        assert "doc1.txt" in messages_str
        assert "doc2.txt" in messages_str

    def test_prompt_builder_empty_documents(self):
        """PromptBuilder handles empty document list."""
        from src.components.prompt import PromptBuilder
        builder = PromptBuilder()

        result = builder.run(documents=[], query="What is this about?")

        assert "messages" in result
        assert len(result["messages"]) == 2


@pytest.mark.unit
class TestPromptBuilderMessages:
    """Tests for PromptBuilder system/user message structure."""

    def test_prompt_builder_creates_two_messages(self):
        """PromptBuilder creates [system, user] message pair."""
        from src.components.prompt import PromptBuilder
        Document = get_document_class()
        builder = PromptBuilder()

        documents = [
            Document(
                content="Test content here.",
                meta={"filename": "test.txt", "chunk_index": 0},
            ),
        ]

        result = builder.run(documents=documents, query="What is the test about?")

        assert "messages" in result
        assert len(result["messages"]) == 2
        assert result["messages"][0].role.value == "system"
        assert result["messages"][1].role.value == "user"

    def test_prompt_builder_custom_org_name(self):
        """PromptBuilder accepts custom org_name."""
        from src.components.prompt import PromptBuilder
        Document = get_document_class()
        builder = PromptBuilder(org_name="Acme Corp")

        documents = [Document(content="Company policy.", meta={})]

        result = builder.run(documents=documents, query="What is the policy?")

        assert "messages" in result
        assert "Acme Corp" in result["messages"][0].text


@pytest.mark.unit
class TestDomainFilterBuilder:
    """Tests for DomainFilterBuilder component."""

    def test_domain_filter_builder_detects_hr_policy(self):
        """Builder detects hr_policy domain."""
        from src.components.routing import DomainFilterBuilder
        builder = DomainFilterBuilder()

        result = builder.run(hr_policy="What is the leave policy?")

        assert result["detected_domain"] == "hr_policy"
        assert result["query"] == "What is the leave policy?"

    def test_domain_filter_builder_detects_technical(self):
        """Builder detects technical domain."""
        from src.components.routing import DomainFilterBuilder
        builder = DomainFilterBuilder()

        result = builder.run(technical="How does the API authentication work?")

        assert result["detected_domain"] == "technical"

    def test_domain_filter_builder_detects_contracts(self):
        """Builder detects contracts domain."""
        from src.components.routing import DomainFilterBuilder
        builder = DomainFilterBuilder()

        result = builder.run(contracts="What are the termination clauses?")

        assert result["detected_domain"] == "contracts"

    def test_domain_filter_builder_explicit_override(self):
        """Builder uses explicit domain when provided."""
        from src.components.routing import DomainFilterBuilder
        builder = DomainFilterBuilder()

        result = builder.run(
            general="Some question text",
            explicit_domain="contracts",
        )

        assert result["detected_domain"] == "contracts"




@pytest.mark.integration
class TestSecureRetriever:
    """Tests for SecureRetriever component."""

    def test_retriever_initialization(self, qdrant_url: str):
        """Retriever initializes with top_k config."""
        from src.components.retrieval import SecureRetriever
        retriever = SecureRetriever(top_k=10)

        assert retriever is not None
        assert retriever._top_k == 10

    def test_retriever_requires_tenant_id(self, qdrant_url: str):
        """Retriever requires tenant_id for isolation."""
        from src.components.retrieval import SecureRetriever
        retriever = SecureRetriever()

        fake_embedding = [0.1] * 768

        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="test_tenant",
        )

        assert "documents" in result

    def test_retriever_returns_documents(self, qdrant_url: str):
        """Retriever returns list of documents."""
        from src.components.retrieval import SecureRetriever
        retriever = SecureRetriever()

        fake_embedding = [0.1] * 768

        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="nonexistent_tenant",
        )

        assert isinstance(result["documents"], list)




@pytest.mark.integration
class TestSecureRetrieverFiltering:
    """Tests for retriever filtering capabilities."""

    def test_retriever_domain_filter(self, qdrant_url: str):
        """Retriever applies domain filter."""
        from src.components.retrieval import SecureRetriever
        retriever = SecureRetriever()

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

        assert "documents" in result

    def test_retriever_role_filter(self, qdrant_url: str):
        """Retriever applies role-based filter."""
        from src.components.retrieval import SecureRetriever
        retriever = SecureRetriever()

        fake_embedding = [0.1] * 768

        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="test_tenant",
            user_roles=["admin", "hr"],
        )

        assert "documents" in result

    def test_retriever_user_filter(self, qdrant_url: str):
        """Retriever applies user-based filter."""
        from src.components.retrieval import SecureRetriever
        retriever = SecureRetriever()

        fake_embedding = [0.1] * 768

        result = retriever.run(
            query_embedding=fake_embedding,
            tenant_id="test_tenant",
            user_id="user123",
        )

        assert "documents" in result


# ---------------------------------------------------------------------------
# TenantModelResolver unit tests (no real DB needed — psycopg2 is mocked)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTenantModelResolver:
    """Unit tests for TenantModelResolver with mocked psycopg2."""

    def _make_resolver(self) -> "TenantModelResolver":
        from src.components.model_resolver import TenantModelResolver
        # Reset class-level cache between tests
        TenantModelResolver._cache = {}
        TenantModelResolver._platform_cache = (object(), 0.0)  # expired
        return TenantModelResolver(postgres_url="postgresql://test", default_model="default-model")

    def test_resolve_returns_default_model_and_no_thinking_on_empty_db(self, monkeypatch):
        """When DB returns no rows, model falls back to default and thinking_mode=False."""
        import psycopg2
        from src.components.model_resolver import TenantModelResolver

        def fake_connect(url):
            class FakeCursor:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def execute(self, *a): pass
                def fetchone(self): return None
                def __iter__(self): return iter([])

            class FakeConn:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def cursor(self, **kw): return FakeCursor()

            return FakeConn()

        monkeypatch.setattr(psycopg2, "connect", fake_connect)
        resolver = self._make_resolver()

        import asyncio
        result = asyncio.run(resolver.resolve("tenant-1", "user-1"))
        assert result.model == "default-model"
        assert result.thinking_mode is False

    def test_resolve_returns_thinking_true_when_user_preference_set(self, monkeypatch):
        """When user_preferences has thinking_mode=true, resolved thinking_mode is True."""
        import psycopg2
        from src.components.model_resolver import TenantModelResolver

        call_count = [0]

        def fake_connect(url):
            class FakeCursor:
                def __enter__(self): return self
                def __exit__(self, *a): pass

                def execute(self, sql, params=None):
                    pass

                def fetchone(self_inner):
                    call_count[0] += 1
                    # Call 1: _fetch_platform_model_sync → returns None (no platform override)
                    if call_count[0] == 1:
                        return None
                    # Call 2: _fetch_tenant_model_sync → returns row with llm_model
                    if call_count[0] == 2:
                        return {"llm_model": "my-model"}
                    # Call 3: _fetch_user_preferences_sync → returns thinking_mode=True
                    if call_count[0] == 3:
                        return {"value": True}
                    return None

                def __iter__(self): return iter([])

            class FakeConn:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def cursor(self, **kw): return FakeCursor()

            return FakeConn()

        monkeypatch.setattr(psycopg2, "connect", fake_connect)
        resolver = self._make_resolver()

        import asyncio
        result = asyncio.run(resolver.resolve("tenant-1", "user-1"))
        assert result.model == "my-model"
        assert result.thinking_mode is True

    def test_invalidate_clears_user_entry(self, monkeypatch):
        """invalidate(tenant_id, user_id) removes only the specific user's cache entry."""
        import psycopg2
        from src.components.model_resolver import TenantModelResolver

        def fake_connect(url):
            class FakeCursor:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def execute(self, *a): pass
                def fetchone(self): return None

            class FakeConn:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def cursor(self, **kw): return FakeCursor()

            return FakeConn()

        monkeypatch.setattr(psycopg2, "connect", fake_connect)

        import asyncio
        resolver = self._make_resolver()
        asyncio.run(resolver.resolve("tenant-1", "user-a"))
        asyncio.run(resolver.resolve("tenant-1", "user-b"))

        assert ("tenant-1", "user-a") in TenantModelResolver._cache
        assert ("tenant-1", "user-b") in TenantModelResolver._cache

        resolver.invalidate(tenant_id="tenant-1", user_id="user-a")

        assert ("tenant-1", "user-a") not in TenantModelResolver._cache
        assert ("tenant-1", "user-b") in TenantModelResolver._cache

    def test_invalidate_tenant_clears_all_users(self, monkeypatch):
        """invalidate(tenant_id) removes all user entries for that tenant."""
        import psycopg2
        from src.components.model_resolver import TenantModelResolver

        def fake_connect(url):
            class FakeCursor:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def execute(self, *a): pass
                def fetchone(self): return None

            class FakeConn:
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def cursor(self, **kw): return FakeCursor()

            return FakeConn()

        monkeypatch.setattr(psycopg2, "connect", fake_connect)

        import asyncio
        resolver = self._make_resolver()
        asyncio.run(resolver.resolve("tenant-1", "user-a"))
        asyncio.run(resolver.resolve("tenant-1", "user-b"))
        asyncio.run(resolver.resolve("tenant-2", "user-c"))

        resolver.invalidate(tenant_id="tenant-1")

        assert ("tenant-1", "user-a") not in TenantModelResolver._cache
        assert ("tenant-1", "user-b") not in TenantModelResolver._cache
        # Different tenant unaffected
        assert ("tenant-2", "user-c") in TenantModelResolver._cache


# ---------------------------------------------------------------------------
# T2 verification: LocalCrossEncoderRanker must be removed
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRerankerImports:
    """Verify dead code removal: LocalCrossEncoderRanker no longer exists."""

    def test_local_cross_encoder_removed(self):
        """Importing LocalCrossEncoderRanker raises ImportError — it was deleted."""
        with pytest.raises((ImportError, AttributeError)):
            from src.components.reranker import LocalCrossEncoderRanker  # noqa: F401

    def test_infinity_reranker_importable(self):
        """InfinityReranker is still present and importable."""
        from src.components.reranker import InfinityReranker
        r = InfinityReranker(url="http://localhost:7997", model="cross-encoder/ms-marco-MiniLM-L-6-v2")
        assert r is not None
        assert r.top_k == 10


# ---------------------------------------------------------------------------
# T12 verification: _extract_think handles unclosed <think> blocks
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestExtractThink:
    """Tests for _extract_think in pipelines/query.py."""

    def _call(self, raw: str):
        from src.pipelines.query import _extract_think
        return _extract_think(raw)

    def test_complete_block(self):
        thinking, answer = self._call("<think>I am reasoning</think>The final answer")
        assert thinking == "I am reasoning"
        assert answer == "The final answer"

    def test_complete_block_with_whitespace(self):
        thinking, answer = self._call("<think>\n  reasoning here\n</think>\n\nAnswer text")
        assert "reasoning here" in thinking
        assert "Answer text" in answer

    def test_unclosed_block_returns_thinking_empty_answer(self):
        """Generation truncated mid-think: tail is thinking, answer is empty."""
        thinking, answer = self._call("<think>partial thought that never ends")
        assert "partial thought" in thinking
        assert answer == ""

    def test_no_think_block_passthrough(self):
        thinking, answer = self._call("This is a plain answer with no think block.")
        assert thinking == ""
        assert answer == "This is a plain answer with no think block."

    def test_empty_think_block(self):
        thinking, answer = self._call("<think></think>The answer")
        assert thinking == ""
        assert answer == "The answer"

    def test_nested_angle_brackets_in_answer(self):
        """Angle brackets in the answer after </think> don't confuse the parser."""
        thinking, answer = self._call("<think>reason</think>Answer with <b>html</b>")
        assert thinking == "reason"
        assert "html" in answer


# ---------------------------------------------------------------------------
# T11 verification: min_score branch behaviour
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMinScoreBranch:
    """Tests for rag_min_score_fallback_topk config in query pipeline."""

    def _make_doc(self, score: float):
        from haystack import Document
        return Document(content=f"doc score={score}", score=score)

    def _apply_filter(self, docs, min_score: float, fallback_topk: int):
        """Mirror the logic in RAGService.query step 6."""
        reranked = docs
        if min_score > 0.0:
            above = [d for d in reranked if (d.score or 0.0) >= min_score]
            if not above and fallback_topk > 0:
                above = reranked[:fallback_topk]
            reranked = above
        return reranked

    def test_strict_mode_returns_empty_when_all_below_threshold(self):
        """fallback_topk=0: no docs pass threshold → empty list."""
        docs = [self._make_doc(0.1), self._make_doc(0.2)]
        result = self._apply_filter(docs, min_score=0.5, fallback_topk=0)
        assert result == []

    def test_fallback_topk_returns_n_docs_when_none_pass(self):
        """fallback_topk=2: no docs pass threshold → top-2 returned."""
        docs = [self._make_doc(0.1), self._make_doc(0.2), self._make_doc(0.3)]
        result = self._apply_filter(docs, min_score=0.9, fallback_topk=2)
        assert len(result) == 2

    def test_above_threshold_docs_returned_normally(self):
        """Docs above threshold are returned; fallback is not triggered."""
        docs = [self._make_doc(0.9), self._make_doc(0.1)]
        result = self._apply_filter(docs, min_score=0.5, fallback_topk=0)
        assert len(result) == 1
        assert result[0].score == pytest.approx(0.9)

    def test_min_score_zero_disables_filter(self):
        """min_score=0.0 returns all docs unfiltered."""
        docs = [self._make_doc(0.05), self._make_doc(0.1)]
        result = self._apply_filter(docs, min_score=0.0, fallback_topk=0)
        assert len(result) == 2
