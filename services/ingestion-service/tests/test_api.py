"""
FastAPI TestClient tests for the ingestion-service API.

Auth bypass: we override `require_internal_token` and `get_tenant_id` so tests
don't need real HMAC headers. This tests routing + model validation, not auth
middleware (which is covered separately).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app, get_tenant_id, require_internal_token


# ---------------------------------------------------------------------------
# Test client with auth bypassed
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    app.dependency_overrides[require_internal_token] = lambda: None
    app.dependency_overrides[get_tenant_id] = lambda: "test-tenant"
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "ingestion-service"


# ---------------------------------------------------------------------------
# POST /ingest
# ---------------------------------------------------------------------------


def test_ingest_returns_202_and_accepted(client: TestClient) -> None:
    doc_id = str(uuid.uuid4())
    with patch("src.api.main._ingest_document_background", new_callable=AsyncMock):
        response = client.post(
            "/ingest",
            json={
                "document_id": doc_id,
                "bucket": "docintel-test-tenant",
                "object_path": "docs/abc/original.txt",
                "filename": "test.txt",
                "domain_hint": "auto",
            },
        )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["document_id"] == doc_id


def test_ingest_missing_bucket_returns_422(client: TestClient) -> None:
    response = client.post(
        "/ingest",
        json={"document_id": str(uuid.uuid4()), "object_path": "docs/abc/file.txt"},
    )
    assert response.status_code == 422


def test_ingest_missing_object_path_returns_422(client: TestClient) -> None:
    response = client.post(
        "/ingest",
        json={"document_id": str(uuid.uuid4()), "bucket": "docintel-test-tenant"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /vectors/{tenant_id}/{document_id}
# ---------------------------------------------------------------------------


def test_delete_document_vectors_returns_200(client: TestClient) -> None:
    tid = "test-tenant"
    did = str(uuid.uuid4())
    with (
        patch("src.api.main.delete_document_from_store") as mock_store,
        patch("src.api.main.invalidate_cache_for_tenant") as mock_cache,
    ):
        response = client.delete(f"/vectors/{tid}/{did}")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["document_id"] == did
    mock_store.assert_called_once_with(tid, did)
    mock_cache.assert_called_once_with(tid)


def test_delete_document_vectors_cross_tenant_rejected(client: TestClient) -> None:
    """Caller tenant != path tenant must be rejected."""
    app.dependency_overrides[get_tenant_id] = lambda: "tenant-a"
    with (
        patch("src.api.main.delete_document_from_store"),
        patch("src.api.main.invalidate_cache_for_tenant"),
    ):
        response = client.delete(f"/vectors/tenant-b/{uuid.uuid4()}")
    app.dependency_overrides[get_tenant_id] = lambda: "test-tenant"
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /vectors/{tenant_id}
# ---------------------------------------------------------------------------


def test_delete_tenant_vectors_returns_200(client: TestClient) -> None:
    tid = "test-tenant"
    with (
        patch("src.api.main.delete_tenant_from_store") as mock_store,
        patch("src.api.main.invalidate_cache_for_tenant") as mock_cache,
        patch("src.api.main.invalidate_pipeline_cache") as mock_pipeline,
    ):
        response = client.delete(f"/vectors/{tid}")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["tenant_id"] == tid
    mock_store.assert_called_once_with(tid)
    mock_cache.assert_called_once_with(tid)
    mock_pipeline.assert_called_once_with(tid)
