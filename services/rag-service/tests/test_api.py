"""
API Endpoint Tests
==================

Tests for FastAPI endpoints.
"""

import asyncio
import pytest
import uuid
from fastapi.testclient import TestClient
from typing import Generator

from src.api.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """Create FastAPI test client with minimal app.state populated."""
    from unittest.mock import AsyncMock, MagicMock
    from src.config import Settings
    from src.api.dependencies import require_internal_token
    from src.components.model_resolver import TenantResolved
    # Populate app.state directly so endpoints that depend on Settings/RAGService
    # work without triggering the full lifespan (which connects to external services).
    settings = Settings()
    app.state.settings = settings
    app.state.llm_semaphore = asyncio.Semaphore(settings.llm_concurrency_limit)
    app.state.tracer = MagicMock()
    app.state.model_profile_resolver = MagicMock()
    app.state.summarizer = MagicMock()
    app.state.http = MagicMock()
    app.state.analytics_bus = MagicMock()
    # model_resolver.resolve is async — use AsyncMock so await works
    mock_resolver = MagicMock()
    mock_resolver.resolve = AsyncMock(
        return_value=TenantResolved(model="test-model", thinking_mode=False)
    )
    app.state.model_resolver = mock_resolver
    # rag_service.query is async; raise so query endpoint returns 500 (acceptable)
    mock_rag = MagicMock()
    mock_rag.query = AsyncMock(side_effect=RuntimeError("LLM not available in test"))
    app.state.rag_service = mock_rag
    # Bypass HMAC internal-token auth so validation/routing tests reach their target.
    app.dependency_overrides[require_internal_token] = lambda: None
    # raise_server_exceptions=False: unhandled server errors return HTTP 500
    # rather than re-raising inside the test.
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.unit
class TestHealthEndpoint:
    """Tests for health check endpoint."""

    def test_health_check(self, client: TestClient, monkeypatch):
        """Health endpoint returns 200 OK without requiring a live Qdrant/LLM."""
        import qdrant_client as qdrant_client_module

        class FakeQdrantClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_collections(self):
                return None

        monkeypatch.setattr(qdrant_client_module, "QdrantClient", FakeQdrantClient)

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data


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


@pytest.mark.unit
class TestPublishQueryEvent:
    """A7: query telemetry publishes to the analytics.query Redis stream
    instead of an HTTP POST to analytics-service."""

    async def _run(self, bus, **overrides):
        from src.api.main import _publish_query_event

        kwargs = dict(
            query_id="q1",
            tenant_id="tenant-1",
            user_id="user-1",
            latency_ms=100,
            model_used="test-model",
            cache_hit=False,
            source_count=3,
        )
        kwargs.update(overrides)
        await _publish_query_event(bus, **kwargs)

    def test_publishes_to_analytics_query_topic_with_maxlen(self):
        from unittest.mock import AsyncMock
        from docintel_common.messaging import TOPIC_ANALYTICS_QUERY

        bus = AsyncMock()
        asyncio.run(self._run(bus, prompt_tokens=10, completion_tokens=5, cost_usd=0.001))

        bus.publish.assert_awaited_once()
        topic, payload = bus.publish.call_args.args
        assert topic == TOPIC_ANALYTICS_QUERY
        assert payload["query_id"] == "q1"
        assert payload["prompt_tokens"] == 10
        assert payload["completion_tokens"] == 5
        assert payload["cost_usd"] == 0.001
        assert bus.publish.call_args.kwargs["maxlen"] == 100_000

    def test_publish_failure_is_swallowed_not_raised(self):
        """Fire-and-forget: a Redis outage must never surface to the caller."""
        from unittest.mock import AsyncMock

        bus = AsyncMock()
        bus.publish.side_effect = RuntimeError("redis down")

        asyncio.run(self._run(bus))  # must not raise


