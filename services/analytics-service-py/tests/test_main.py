"""
Unit tests for analytics-service-py endpoints.
Mocks ClickHouse to avoid external dependencies.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from src.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_clickhouse():
    """Replace ClickHouse client with a mock so tests run without a live CH instance."""
    mock_client = MagicMock()
    # Default return for INSERT operations
    mock_client.insert.return_value = None
    with patch("src.main.get_client", return_value=mock_client), \
         patch("src.db.ensure_schema"):
        yield mock_client


class TestHealthEndpoint:
    def test_health_connected(self, client, mock_clickhouse):
        mock_clickhouse.command.return_value = 1
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["clickhouse"] == "connected"

    def test_health_degraded_when_ch_fails(self, client, mock_clickhouse):
        mock_clickhouse.command.side_effect = RuntimeError("connection refused")
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert "error" in body["clickhouse"]


class TestQueryEventIngestion:
    def test_ingest_query_event(self, client, mock_clickhouse):
        payload = {
            "query_id": "q1",
            "tenant_id": "alpha",
            "user_id": "u1",
            "latency_ms": 123,
            "model_used": "qwen3.5:4b",
            "cache_hit": False,
            "source_count": 5,
        }
        resp = client.post("/events/query", json=payload)
        assert resp.status_code == 202
        mock_clickhouse.insert.assert_called_once()
        call_kwargs = mock_clickhouse.insert.call_args
        assert "query_events" in call_kwargs[0][0]
        # Verify thinking_truncated is included in the insert column list
        columns = call_kwargs[1].get("column_names") or call_kwargs[0][2]
        assert "thinking_truncated" in columns

    def test_ingest_query_event_with_thinking_truncated_true(self, client, mock_clickhouse):
        payload = {
            "query_id": "q-trunc", "tenant_id": "alpha", "user_id": "u1",
            "latency_ms": 180000, "model_used": "qwen3.5:4b",
            "cache_hit": False, "source_count": 5,
            "thinking_truncated": True,
        }
        resp = client.post("/events/query", json=payload)
        assert resp.status_code == 202
        call_kwargs = mock_clickhouse.insert.call_args
        row = call_kwargs[0][1][0]
        columns = call_kwargs[1].get("column_names") or call_kwargs[0][2]
        # thinking_truncated value must be True in the inserted row
        assert row[columns.index("thinking_truncated")] is True

    def test_ingest_query_event_includes_tokens_and_cost(self, client, mock_clickhouse):
        """A5: prompt/completion tokens and cost_usd are inserted alongside the event."""
        payload = {
            "query_id": "q-cost", "tenant_id": "alpha", "user_id": "u1",
            "latency_ms": 500, "model_used": "qwen3.5:4b",
            "cache_hit": False, "source_count": 3,
            "prompt_tokens": 120, "completion_tokens": 45, "cost_usd": 0.0021,
        }
        resp = client.post("/events/query", json=payload)
        assert resp.status_code == 202
        call_kwargs = mock_clickhouse.insert.call_args
        row = call_kwargs[0][1][0]
        columns = call_kwargs[1].get("column_names") or call_kwargs[0][2]
        assert row[columns.index("prompt_tokens")] == 120
        assert row[columns.index("completion_tokens")] == 45
        assert row[columns.index("cost_usd")] == pytest.approx(0.0021)

    def test_ingest_query_event_defaults_tokens_and_cost_to_zero(self, client, mock_clickhouse):
        """Older callers that don't send token fields still insert cleanly with defaults."""
        payload = {
            "query_id": "q-notokens", "tenant_id": "alpha", "user_id": "u1",
            "latency_ms": 100, "model_used": "qwen3.5:4b",
            "cache_hit": False, "source_count": 1,
        }
        resp = client.post("/events/query", json=payload)
        assert resp.status_code == 202
        call_kwargs = mock_clickhouse.insert.call_args
        row = call_kwargs[0][1][0]
        columns = call_kwargs[1].get("column_names") or call_kwargs[0][2]
        assert row[columns.index("prompt_tokens")] == 0
        assert row[columns.index("completion_tokens")] == 0
        assert row[columns.index("cost_usd")] == 0.0

    def test_ingest_query_event_ch_failure_returns_500(self, client, mock_clickhouse):
        mock_clickhouse.insert.side_effect = RuntimeError("CH down")
        payload = {
            "query_id": "q2", "tenant_id": "alpha", "user_id": "u2",
            "latency_ms": 0, "model_used": "none", "cache_hit": False, "source_count": 0,
        }
        resp = client.post("/events/query", json=payload)
        assert resp.status_code == 500


class TestFeedbackEventIngestion:
    def test_ingest_feedback_event(self, client, mock_clickhouse):
        payload = {
            "query_id": "q1",
            "tenant_id": "alpha",
            "user_id": "u1",
            "liked": True,
            "comment": "great answer",
        }
        resp = client.post("/events/feedback", json=payload)
        assert resp.status_code == 202
        mock_clickhouse.insert.assert_called_once()
        call_kwargs = mock_clickhouse.insert.call_args
        assert "feedback_events" in call_kwargs[0][0]


class TestAnalyticsEndpoints:
    def _mock_query_result(self, mock_ch, row):
        result = MagicMock()
        result.first_row = row
        mock_ch.query.return_value = result

    def test_feedback_summary_no_tenant(self, client, mock_clickhouse):
        self._mock_query_result(mock_clickhouse, (10, 2, 12))
        resp = client.get("/analytics/feedback/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["liked"] == 10
        assert body["disliked"] == 2
        assert body["total"] == 12
        # Should NOT include a tenant_id param in the query
        call_args = mock_clickhouse.query.call_args
        assert "parameters" not in call_args.kwargs or call_args.kwargs.get("parameters") is None

    def test_feedback_summary_with_header_tenant(self, client, mock_clickhouse):
        self._mock_query_result(mock_clickhouse, (5, 1, 6))
        resp = client.get(
            "/analytics/feedback/summary",
            headers={"X-Tenant-Id": "alpha"},
        )
        assert resp.status_code == 200
        call_args = mock_clickhouse.query.call_args
        assert call_args.kwargs.get("parameters") == {"tenant_id": "alpha"}

    def test_queries_summary(self, client, mock_clickhouse):
        self._mock_query_result(mock_clickhouse, (100, 1250.5, 0.3))
        resp = client.get("/analytics/queries/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_queries"] == 100
        assert body["avg_latency_ms"] == 1250.5
        assert body["cache_hit_rate"] == 0.3

    def test_queries_summary_ch_failure(self, client, mock_clickhouse):
        mock_clickhouse.query.side_effect = RuntimeError("CH timeout")
        resp = client.get("/analytics/queries/summary")
        assert resp.status_code == 500


class TestRoleAwareTenantScoping:
    """
    Ported from the Kotlin analytics-service: platform_admin gets global
    aggregates; every other role is forced to their own tenant (X-Tenant-Id
    header) regardless of what the tenant_id query parameter says.
    """

    def _mock_query_result(self, mock_ch, row):
        result = MagicMock()
        result.first_row = row
        mock_ch.query.return_value = result

    def test_platform_admin_sees_global_stats_ignoring_tenant_header(self, client, mock_clickhouse):
        self._mock_query_result(mock_clickhouse, (100, 1250.5, 0.3))
        resp = client.get(
            "/analytics/queries/summary",
            headers={"X-Tenant-Id": "alpha", "X-User-Role": "platform_admin"},
        )
        assert resp.status_code == 200
        call_args = mock_clickhouse.query.call_args
        assert "parameters" not in call_args.kwargs or call_args.kwargs.get("parameters") is None

    def test_tenant_admin_forced_to_own_tenant_header(self, client, mock_clickhouse):
        self._mock_query_result(mock_clickhouse, (5, 200.0, 0.1))
        resp = client.get(
            "/analytics/queries/summary",
            headers={"X-Tenant-Id": "alpha", "X-User-Role": "tenant_admin"},
        )
        assert resp.status_code == 200
        call_args = mock_clickhouse.query.call_args
        assert call_args.kwargs.get("parameters") == {"tenant_id": "alpha"}

    def test_tenant_admin_cannot_escalate_via_query_param(self, client, mock_clickhouse):
        """A tenant_admin passing ?tenant_id=beta must still be scoped to their
        own X-Tenant-Id header (alpha), never the query-param value."""
        self._mock_query_result(mock_clickhouse, (5, 200.0, 0.1))
        resp = client.get(
            "/analytics/queries/summary",
            params={"tenant_id": "beta"},
            headers={"X-Tenant-Id": "alpha", "X-User-Role": "tenant_admin"},
        )
        assert resp.status_code == 200
        call_args = mock_clickhouse.query.call_args
        assert call_args.kwargs.get("parameters") == {"tenant_id": "alpha"}


class TestSqlInjectionProtection:
    """Ensure tenant_id from X-Tenant-Id header is parameterized, not interpolated."""

    def _mock_query_result(self, mock_ch, row):
        result = MagicMock()
        result.first_row = row
        mock_ch.query.return_value = result

    def test_malicious_tenant_id_not_interpolated(self, client, mock_clickhouse):
        self._mock_query_result(mock_clickhouse, (0, 0, 0))
        malicious = "'; DROP TABLE feedback_events; --"
        resp = client.get(
            "/analytics/feedback/summary",
            headers={"X-Tenant-Id": malicious},
        )
        assert resp.status_code == 200
        call_args = mock_clickhouse.query.call_args
        # The malicious string must appear ONLY in parameters, never in the raw SQL string
        raw_sql = call_args[0][0]
        assert malicious not in raw_sql
        assert call_args.kwargs.get("parameters") == {"tenant_id": malicious}
