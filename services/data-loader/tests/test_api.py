"""
FastAPI integration tests for the data-loader service.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "data-loader"


# ---------------------------------------------------------------------------
# GET /datasets
# ---------------------------------------------------------------------------


def test_list_datasets_requires_user_id(client: TestClient) -> None:
    from src.api.main import app, require_user_id

    app.dependency_overrides.pop(require_user_id, None)
    response = client.get("/datasets")
    assert response.status_code == 403
    app.dependency_overrides[require_user_id] = lambda: None


def test_list_datasets_returns_available_datasets(client: TestClient) -> None:
    response = client.get("/datasets", headers={"X-User-Id": "test-user"})
    assert response.status_code == 200
    body = response.json()
    keys = {d["key"] for d in body["available_datasets"]}
    assert {"techqa", "hr_policies", "cuad"} == keys


# ---------------------------------------------------------------------------
# POST /datasets/load
# ---------------------------------------------------------------------------


def test_start_load_missing_user_id_returns_403(client: TestClient) -> None:
    from src.api.main import app, require_user_id

    app.dependency_overrides.pop(require_user_id, None)
    response = client.post("/datasets/load", json={"datasets": ["cuad"]})
    assert response.status_code == 403
    app.dependency_overrides[require_user_id] = lambda: None


def test_start_load_empty_datasets_returns_400(client: TestClient) -> None:
    response = client.post(
        "/datasets/load",
        json={"datasets": []},
        headers={"X-User-Id": "u1", "X-Tenant-Id": "tenant-1"},
    )
    assert response.status_code == 400


def test_start_load_unknown_dataset_returns_400(client: TestClient) -> None:
    response = client.post(
        "/datasets/load",
        json={"datasets": ["nonexistent_dataset"]},
        headers={"X-User-Id": "u1", "X-Tenant-Id": "tenant-1"},
    )
    assert response.status_code == 400
    assert "nonexistent_dataset" in response.json()["detail"]


def test_start_load_returns_202_with_job_id(client: TestClient) -> None:
    with patch("src.api.main._run_bulk_load", new_callable=AsyncMock):
        response = client.post(
            "/datasets/load",
            json={"datasets": ["techqa"], "samples_per_dataset": 5},
            headers={"X-User-Id": "u1", "X-Tenant-Id": "tenant-1"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "started"
    assert body["datasets"] == ["techqa"]
    # message carries the job_id (UUID4)
    job_id = body["message"]
    parsed = uuid.UUID(job_id, version=4)
    assert str(parsed) == job_id


# ---------------------------------------------------------------------------
# GET /datasets/load/{job_id}/progress
# ---------------------------------------------------------------------------


def test_progress_unknown_job_returns_404(client: TestClient) -> None:
    bad_id = str(uuid.uuid4())
    response = client.get(
        f"/datasets/load/{bad_id}/progress",
        headers={"X-User-Id": "u1", "X-Tenant-Id": "tenant-1"},
    )
    assert response.status_code == 404


def test_progress_requires_user_id(client: TestClient) -> None:
    from src.api.main import app, require_user_id

    app.dependency_overrides.pop(require_user_id, None)
    response = client.get(
        f"/datasets/load/{uuid.uuid4()}/progress",
        headers={"X-Tenant-Id": "tenant-1"},
    )
    assert response.status_code == 403
    app.dependency_overrides[require_user_id] = lambda: None
