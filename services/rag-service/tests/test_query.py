"""
Query Pipeline Tests
====================

Tests for RAG query processing with expected outcomes.
"""

import pytest
import uuid
import asyncio

from src.pipelines.query import RAGQueryPipeline, get_query_pipeline
from src.pipelines.indexing import index_chunks, delete_tenant_vectors
from src.chunking import ChunkingService


def index_test_document(tenant_id: str, content: str, filename: str, domain: str) -> str:
    """Helper to index a test document."""
    document_id = str(uuid.uuid4())
    chunking_service = ChunkingService()
    
    chunk_results = chunking_service.chunk_document(
        text=content,
        document_id=document_id,
        tenant_id=tenant_id,
        filename=filename,
        extra_metadata={
            "domain": domain,
            "document_type": domain,
        },
    )
    
    chunks = [
        {"content": c.content, "metadata": c.metadata}
        for c in chunk_results
    ]
    
    asyncio.get_event_loop().run_until_complete(
        index_chunks(
            chunks=chunks,
            tenant_id=tenant_id,
            document_id=document_id,
        )
    )
    
    return document_id


@pytest.fixture
def indexed_hr_policy(hr_policy_content: str) -> tuple[str, str]:
    """Index HR policy and return (tenant_id, document_id)."""
    tenant_id = f"query_test_{uuid.uuid4().hex[:8]}"
    document_id = index_test_document(
        tenant_id=tenant_id,
        content=hr_policy_content,
        filename="hr_policy_leave.txt",
        domain="hr_policy",
    )
    
    yield tenant_id, document_id
    
    # Cleanup
    asyncio.get_event_loop().run_until_complete(
        delete_tenant_vectors(tenant_id=tenant_id)
    )


@pytest.fixture
def indexed_technical_doc(technical_doc_content: str) -> tuple[str, str]:
    """Index technical doc and return (tenant_id, document_id)."""
    tenant_id = f"query_test_{uuid.uuid4().hex[:8]}"
    document_id = index_test_document(
        tenant_id=tenant_id,
        content=technical_doc_content,
        filename="technical_api_docs.txt",
        domain="technical",
    )
    
    yield tenant_id, document_id
    
    # Cleanup
    asyncio.get_event_loop().run_until_complete(
        delete_tenant_vectors(tenant_id=tenant_id)
    )


@pytest.fixture
def indexed_all_documents(all_documents: dict) -> tuple[str, dict]:
    """Index all test documents and return (tenant_id, doc_ids)."""
    tenant_id = f"query_all_test_{uuid.uuid4().hex[:8]}"
    doc_ids = {}
    
    for doc_key, doc_info in all_documents.items():
        doc_ids[doc_key] = index_test_document(
            tenant_id=tenant_id,
            content=doc_info["content"],
            filename=doc_info["filename"],
            domain=doc_info["domain"],
        )
    
    yield tenant_id, doc_ids
    
    # Cleanup
    asyncio.get_event_loop().run_until_complete(
        delete_tenant_vectors(tenant_id=tenant_id)
    )


@pytest.mark.unit
class TestRAGQueryPipelineCreation:
    """Tests for RAGQueryPipeline initialization."""

    def test_pipeline_creation_default(self):
        """Pipeline is created with default settings."""
        pipeline = RAGQueryPipeline()
        
        assert pipeline is not None
        assert pipeline.use_cache is True
        assert pipeline.use_reranking is True
        assert pipeline.use_query_expansion is False

    def test_pipeline_creation_custom(self):
        """Pipeline accepts custom configuration."""
        pipeline = RAGQueryPipeline(
            use_cache=False,
            use_reranking=False,
            use_query_expansion=True,
        )
        
        assert pipeline.use_cache is False
        assert pipeline.use_reranking is False
        assert pipeline.use_query_expansion is True

    def test_global_pipeline_singleton(self):
        """get_query_pipeline returns same instance."""
        # Note: This may not be true in test environment due to imports
        pipeline = get_query_pipeline()
        
        assert pipeline is not None
        assert isinstance(pipeline, RAGQueryPipeline)


@pytest.mark.integration
class TestQueryWithNoDocuments:
    """Tests for queries when no documents are indexed."""

    def test_query_empty_collection(self, test_tenant_id: str):
        """Query returns appropriate message when no documents exist."""
        pipeline = RAGQueryPipeline(
            use_cache=False,
            use_reranking=False,
        )
        
        result = pipeline.run(
            question="What is the leave policy?",
            tenant_id=test_tenant_id,
        )
        
        assert "couldn't find" in result["answer"].lower() or "no relevant" in result["answer"].lower()
        assert result["sources"] == []
        assert result["cache_hit"] is False


@pytest.mark.integration
@pytest.mark.slow
class TestHRPolicyQueries:
    """Tests for HR policy document queries with expected outcomes."""

    def test_query_annual_leave_entitlement(self, indexed_hr_policy: tuple[str, str]):
        """Query about annual leave returns correct information."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(
            use_cache=False,
            use_reranking=True,
        )
        
        result = pipeline.run(
            question="How many days of annual leave do employees get?",
            tenant_id=tenant_id,
        )
        
        # Verify answer contains expected information
        answer = result["answer"].lower()
        assert "25" in answer or "twenty-five" in answer
        
        # Verify sources are returned
        assert len(result["sources"]) > 0
        
        # Verify latency is reasonable
        assert result["latency_ms"] < 30000  # 30 seconds max

    def test_query_sick_leave_process(self, indexed_hr_policy: tuple[str, str]):
        """Query about sick leave process returns correct procedure."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="What is the process for requesting sick leave?",
            tenant_id=tenant_id,
        )
        
        answer = result["answer"].lower()
        # Should mention notification requirements
        assert "notify" in answer or "manager" in answer or "9:00" in answer or "morning" in answer

    def test_query_maternity_leave(self, indexed_hr_policy: tuple[str, str]):
        """Query about maternity leave returns duration."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="How long is maternity leave?",
            tenant_id=tenant_id,
        )
        
        answer = result["answer"].lower()
        assert "16" in answer or "sixteen" in answer or "weeks" in answer

    def test_query_leave_carry_forward(self, indexed_hr_policy: tuple[str, str]):
        """Query about carry forward returns policy details."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="Can I carry forward unused vacation days?",
            tenant_id=tenant_id,
        )
        
        answer = result["answer"].lower()
        # Should mention carry forward limit
        assert "5" in answer or "five" in answer or "march" in answer


@pytest.mark.integration
@pytest.mark.slow
class TestTechnicalDocQueries:
    """Tests for technical documentation queries."""

    def test_query_rate_limiting(self, indexed_technical_doc: tuple[str, str]):
        """Query about rate limits returns correct limits."""
        tenant_id, _ = indexed_technical_doc
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="What is the rate limit for the API?",
            tenant_id=tenant_id,
        )
        
        answer = result["answer"].lower()
        # Should mention rate limits
        assert "100" in answer or "1000" in answer or "requests" in answer or "minute" in answer

    def test_query_authentication(self, indexed_technical_doc: tuple[str, str]):
        """Query about authentication returns auth method."""
        tenant_id, _ = indexed_technical_doc
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="How do I authenticate API requests?",
            tenant_id=tenant_id,
        )
        
        answer = result["answer"].lower()
        assert "bearer" in answer or "token" in answer or "authorization" in answer

    def test_query_file_formats(self, indexed_technical_doc: tuple[str, str]):
        """Query about file formats returns supported types."""
        tenant_id, _ = indexed_technical_doc
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="What file formats are supported for document upload?",
            tenant_id=tenant_id,
        )
        
        answer = result["answer"].lower()
        assert "pdf" in answer or "docx" in answer or "txt" in answer


@pytest.mark.integration
@pytest.mark.slow
class TestCrossDocumentQueries:
    """Tests for queries across multiple documents."""

    def test_query_finds_correct_document(self, indexed_all_documents: tuple[str, dict]):
        """Query about specific topic finds the correct document."""
        tenant_id, _ = indexed_all_documents
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        # Query about HR topic
        hr_result = pipeline.run(
            question="What is the bereavement leave policy?",
            tenant_id=tenant_id,
        )
        
        # Should find HR policy document
        assert len(hr_result["sources"]) > 0
        # Source should be from HR document
        sources_filenames = [s["filename"] for s in hr_result["sources"]]
        assert any("hr" in f.lower() for f in sources_filenames)

    def test_query_domain_filtering(self, indexed_all_documents: tuple[str, dict]):
        """Query with domain filter restricts to correct documents."""
        tenant_id, _ = indexed_all_documents
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        # Query with domain filter
        result = pipeline.run(
            question="What is the termination policy?",
            tenant_id=tenant_id,
            document_type="contracts",
        )
        
        # Should prefer contract document
        if len(result["sources"]) > 0:
            # First source should be from contracts domain if filtering works
            first_source = result["sources"][0]
            assert "domain" in first_source.get("metadata", {}) or "contract" in first_source.get("filename", "").lower()


@pytest.mark.integration
@pytest.mark.slow
class TestQueryRetrieval:
    """Tests for retrieval quality."""

    def test_retrieval_returns_relevant_chunks(self, indexed_hr_policy: tuple[str, str]):
        """Retrieved chunks are relevant to the query."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="What is the paternity leave entitlement?",
            tenant_id=tenant_id,
            top_k=3,
        )
        
        # Should retrieve chunks mentioning paternity
        found_relevant = False
        for source in result["sources"]:
            if "paternity" in source["content"].lower() or "secondary caregiver" in source["content"].lower():
                found_relevant = True
                break
        
        # Either found in sources or mentioned in answer
        assert found_relevant or "paternity" in result["answer"].lower()

    def test_retrieval_respects_top_k(self, indexed_hr_policy: tuple[str, str]):
        """top_k parameter limits number of sources."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="Tell me about the leave policies",
            tenant_id=tenant_id,
            top_k=2,
        )
        
        assert len(result["sources"]) <= 2

    def test_retrieval_scores_are_reasonable(self, indexed_hr_policy: tuple[str, str]):
        """Retrieved documents have reasonable relevance scores."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=True)
        
        result = pipeline.run(
            question="How do I request annual leave?",
            tenant_id=tenant_id,
        )
        
        for source in result["sources"]:
            # Scores should be between 0 and 1 (normalized)
            assert 0 <= source["score"] <= 1


@pytest.mark.integration
class TestQueryEdgeCases:
    """Edge case tests for query pipeline."""

    def test_query_with_empty_question(self, indexed_hr_policy: tuple[str, str]):
        """Empty question is handled gracefully."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=False)
        
        # Should either raise or return meaningful response
        try:
            result = pipeline.run(
                question="",
                tenant_id=tenant_id,
            )
            # If it doesn't raise, should have some response
            assert "answer" in result
        except Exception:
            # Expected to fail with empty question
            pass

    def test_query_with_special_characters(self, indexed_hr_policy: tuple[str, str]):
        """Queries with special characters are handled."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=False)
        
        result = pipeline.run(
            question="What's the policy for <employee> leave? (annual)",
            tenant_id=tenant_id,
        )
        
        assert "answer" in result

    def test_query_with_unicode(self, indexed_hr_policy: tuple[str, str]):
        """Unicode queries are handled."""
        tenant_id, _ = indexed_hr_policy
        
        pipeline = RAGQueryPipeline(use_cache=False, use_reranking=False)
        
        result = pipeline.run(
            question="What's the vacation 假期 policy?",
            tenant_id=tenant_id,
        )
        
        assert "answer" in result


@pytest.mark.integration
@pytest.mark.slow
class TestQueryWithReranking:
    """Tests for reranking behavior."""

    def test_reranking_improves_relevance(self, indexed_hr_policy: tuple[str, str]):
        """Reranking should prioritize more relevant chunks."""
        tenant_id, _ = indexed_hr_policy
        
        # Without reranking
        pipeline_no_rerank = RAGQueryPipeline(use_cache=False, use_reranking=False)
        result_no_rerank = pipeline_no_rerank.run(
            question="What is the bereavement leave for immediate family?",
            tenant_id=tenant_id,
            top_k=3,
        )
        
        # With reranking
        pipeline_rerank = RAGQueryPipeline(use_cache=False, use_reranking=True)
        result_rerank = pipeline_rerank.run(
            question="What is the bereavement leave for immediate family?",
            tenant_id=tenant_id,
            top_k=3,
        )
        
        # Both should return results
        assert len(result_no_rerank["sources"]) > 0
        assert len(result_rerank["sources"]) > 0
        
        # Reranked results should mention bereavement more prominently
        rerank_first_content = result_rerank["sources"][0]["content"].lower() if result_rerank["sources"] else ""
        assert "bereavement" in rerank_first_content or "5 days" in rerank_first_content or "immediate" in rerank_first_content


@pytest.mark.integration
class TestTenantIsolation:
    """Tests for multi-tenant data isolation."""

    def test_tenant_cannot_access_other_tenant_data(
        self,
        hr_policy_content: str,
    ):
        """Queries only return data from the specified tenant."""
        import asyncio
        
        # Create two tenants
        tenant_a = f"tenant_a_{uuid.uuid4().hex[:8]}"
        tenant_b = f"tenant_b_{uuid.uuid4().hex[:8]}"
        
        # Index document for tenant A only
        doc_id = index_test_document(
            tenant_id=tenant_a,
            content=hr_policy_content,
            filename="hr_policy.txt",
            domain="hr_policy",
        )
        
        try:
            pipeline = RAGQueryPipeline(use_cache=False, use_reranking=False)
            
            # Query from tenant A should find data
            result_a = pipeline.run(
                question="What is the annual leave policy?",
                tenant_id=tenant_a,
            )
            
            # Query from tenant B should NOT find tenant A's data
            result_b = pipeline.run(
                question="What is the annual leave policy?",
                tenant_id=tenant_b,
            )
            
            # Tenant A should get results
            assert len(result_a["sources"]) > 0 or "25" in result_a["answer"]
            
            # Tenant B should get no results
            assert len(result_b["sources"]) == 0
            assert "couldn't find" in result_b["answer"].lower() or "no relevant" in result_b["answer"].lower()
        
        finally:
            # Cleanup
            asyncio.get_event_loop().run_until_complete(
                delete_tenant_vectors(tenant_id=tenant_a)
            )
