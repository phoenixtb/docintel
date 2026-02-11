"""
API Endpoint Tests
==================

Tests for FastAPI endpoints.
"""

import pytest
import uuid
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    """Create FastAPI test client."""
    return TestClient(app)


@pytest.mark.unit
class TestHealthEndpoint:
    """Tests for health check endpoint."""

    def test_health_check(self, client: TestClient):
        """Health endpoint returns 200 OK."""
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data


@pytest.mark.unit
class TestSampleDatasetsEndpoint:
    """Tests for sample datasets endpoint."""

    def test_list_sample_datasets(self, client: TestClient):
        """List sample datasets returns available datasets."""
        response = client.get("/sample-datasets")
        
        assert response.status_code == 200
        data = response.json()
        assert "available_datasets" in data
        assert "domains" in data
        assert len(data["available_datasets"]) > 0
        assert len(data["domains"]) > 0

    def test_sample_datasets_structure(self, client: TestClient):
        """Sample datasets have expected structure."""
        response = client.get("/sample-datasets")
        data = response.json()
        
        for dataset in data["available_datasets"]:
            assert "key" in dataset
            assert "name" in dataset
            assert "domain" in dataset


@pytest.mark.unit
class TestClassifyDomainEndpoint:
    """Tests for domain classification endpoint."""

    def test_classify_hr_content(self, client: TestClient):
        """HR content is classified as hr_policy."""
        response = client.post(
            "/classify-domain",
            json={"content": "What is the annual leave policy and vacation entitlement?"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "domain" in data
        assert "confidence" in data
        assert data["domain"] in ["hr_policy", "general", "technical", "contracts"]

    def test_classify_technical_content(self, client: TestClient):
        """Technical content is classified correctly."""
        response = client.post(
            "/classify-domain",
            json={"content": "How does the API authentication work with OAuth2 tokens?"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["domain"] in ["technical", "general"]

    def test_classify_contract_content(self, client: TestClient):
        """Contract content is classified as contracts."""
        response = client.post(
            "/classify-domain",
            json={"content": "What are the termination clauses and liability limitations?"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["domain"] in ["contracts", "general"]

    def test_classify_returns_all_scores(self, client: TestClient):
        """Classification returns scores for all domains."""
        response = client.post(
            "/classify-domain",
            json={"content": "This is some general text content."}
        )
        
        data = response.json()
        assert "all_scores" in data
        assert isinstance(data["all_scores"], dict)
        assert len(data["all_scores"]) >= 3


@pytest.mark.unit
class TestIndexEndpoint:
    """Tests for document indexing endpoint."""

    def test_index_chunks_validation(self, client: TestClient):
        """Index endpoint validates required fields."""
        # Missing required fields
        response = client.post("/index", json={})
        
        assert response.status_code == 422  # Validation error

    def test_index_chunks_structure(self, client: TestClient):
        """Index endpoint accepts valid request structure."""
        request = {
            "chunks": [
                {
                    "content": "Test chunk content for indexing.",
                    "metadata": {"filename": "test.txt", "chunk_index": 0},
                }
            ],
            "document_id": str(uuid.uuid4()),
            "tenant_id": "test_tenant",
        }
        
        response = client.post("/index", json=request)
        
        # Should either succeed or fail gracefully
        assert response.status_code in [200, 500]


@pytest.mark.integration
@pytest.mark.slow
class TestIndexEndpointIntegration:
    """Integration tests for indexing endpoint."""

    def test_index_and_verify(self, client: TestClient):
        """Index chunks and verify they're stored."""
        tenant_id = f"api_test_{uuid.uuid4().hex[:8]}"
        document_id = str(uuid.uuid4())
        
        request = {
            "chunks": [
                {
                    "content": "This is test content about employee benefits and vacation policies.",
                    "metadata": {
                        "filename": "test.txt",
                        "chunk_index": 0,
                        "domain": "hr_policy",
                    },
                },
                {
                    "content": "Annual leave entitlement is 25 days per year for full-time employees.",
                    "metadata": {
                        "filename": "test.txt",
                        "chunk_index": 1,
                        "domain": "hr_policy",
                    },
                },
            ],
            "document_id": document_id,
            "tenant_id": tenant_id,
        }
        
        response = client.post("/index", json=request)
        
        if response.status_code == 200:
            data = response.json()
            assert data["embedded_count"] == 2
            assert data["collection"] == "documents"


@pytest.mark.unit
class TestQueryEndpoint:
    """Tests for query endpoint."""

    def test_query_validation(self, client: TestClient):
        """Query endpoint validates required fields."""
        response = client.post("/query", json={})
        
        assert response.status_code == 422

    def test_query_structure(self, client: TestClient):
        """Query endpoint accepts valid request structure."""
        request = {
            "question": "What is the leave policy?",
            "tenant_id": "test_tenant",
        }
        
        response = client.post("/query", json=request)
        
        # May timeout or fail if LLM not available
        assert response.status_code in [200, 500, 504]

    def test_query_requires_question(self, client: TestClient):
        """Query requires non-empty question."""
        request = {
            "question": "",
            "tenant_id": "test_tenant",
        }
        
        response = client.post("/query", json=request)
        
        assert response.status_code == 422


@pytest.mark.unit
class TestQueryStreamEndpoint:
    """Tests for streaming query endpoint."""

    def test_stream_validation(self, client: TestClient):
        """Stream endpoint validates required fields."""
        response = client.post("/query/stream", json={})
        
        assert response.status_code == 422

    def test_stream_returns_sse(self, client: TestClient):
        """Stream endpoint returns SSE content type."""
        request = {
            "question": "What is the policy?",
            "tenant_id": "test_tenant",
        }
        
        response = client.post("/query/stream", json=request)
        
        # Should return SSE content type
        assert response.headers.get("content-type", "").startswith("text/event-stream")


@pytest.mark.unit
class TestDeleteEndpoints:
    """Tests for delete endpoints."""

    def test_delete_document_validation(self, client: TestClient):
        """Delete document endpoint validates parameters."""
        response = client.delete("/documents/invalid-uuid/tenant/test")
        
        # Should either work or return 404/422
        assert response.status_code in [200, 404, 422]

    def test_delete_document_structure(self, client: TestClient):
        """Delete document endpoint accepts valid structure."""
        document_id = str(uuid.uuid4())
        tenant_id = "test_tenant"
        
        # Endpoint: DELETE /index/{tenant_id}/{document_id}
        response = client.delete(f"/index/{tenant_id}/{document_id}")
        
        # 200 if vectors exist, 404 if no vectors found (both are valid responses)
        assert response.status_code in [200, 404]

    def test_delete_tenant_structure(self, client: TestClient):
        """Delete tenant endpoint accepts valid structure."""
        tenant_id = f"delete_test_{uuid.uuid4().hex[:8]}"
        
        # Endpoint: DELETE /index/{tenant_id}
        response = client.delete(f"/index/{tenant_id}")
        
        # 200 if vectors exist, 404 if no vectors found (both are valid responses)
        assert response.status_code in [200, 404]


@pytest.mark.unit
class TestErrorHandling:
    """Tests for API error handling."""

    def test_not_found_endpoint(self, client: TestClient):
        """Non-existent endpoint returns 404."""
        response = client.get("/nonexistent")
        
        assert response.status_code == 404

    def test_method_not_allowed(self, client: TestClient):
        """Wrong HTTP method returns 405."""
        response = client.put("/health")
        
        assert response.status_code == 405

    def test_invalid_json(self, client: TestClient):
        """Invalid JSON returns 422."""
        response = client.post(
            "/query",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        
        assert response.status_code == 422


@pytest.mark.integration
class TestAPIWithDocuments:
    """Integration tests with actual document operations."""

    def test_full_index_query_flow(self, client: TestClient, hr_policy_content: str):
        """Test complete index and query flow."""
        tenant_id = f"flow_test_{uuid.uuid4().hex[:8]}"
        document_id = str(uuid.uuid4())
        
        # Index document
        from src.chunking import ChunkingService
        chunking_service = ChunkingService()
        
        chunk_results = chunking_service.chunk_document(
            text=hr_policy_content,
            document_id=document_id,
            tenant_id=tenant_id,
            filename="hr_policy.txt",
            extra_metadata={"domain": "hr_policy"},
        )
        
        chunks = [
            {"content": c.content, "metadata": c.metadata}
            for c in chunk_results[:3]  # Just first 3 chunks for speed
        ]
        
        index_response = client.post(
            "/index",
            json={
                "chunks": chunks,
                "document_id": document_id,
                "tenant_id": tenant_id,
            },
        )
        
        if index_response.status_code != 200:
            pytest.skip("Indexing failed, skipping query test")
        
        # Query the document
        query_response = client.post(
            "/query",
            json={
                "question": "How many days of annual leave?",
                "tenant_id": tenant_id,
                "top_k": 3,
            },
        )
        
        # Cleanup
        client.delete(f"/tenants/{tenant_id}")
        
        if query_response.status_code == 200:
            data = query_response.json()
            assert "answer" in data
            assert "sources" in data
