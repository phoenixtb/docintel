"""
Shared fixtures for data-loader tests.

StreamPublisher is patched at module level so the FastAPI lifespan does not
try to connect to a real Redis instance during tests.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Prevent real Redis connections during import / lifespan
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("REDIS_PORT", "6379")
os.environ.setdefault("MINIO_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "minioadmin")
os.environ.setdefault("MINIO_SECRET_KEY", "minioadmin")


@pytest.fixture(scope="module")
def client():
    """
    TestClient with StreamPublisher mocked out.
    The lifespan runs; all Redis calls are intercepted.
    """
    from src.api.main import app, require_user_id

    mock_publisher = AsyncMock()
    mock_publisher.ensure_consumer_group = AsyncMock()
    mock_publisher.publish_file_available = AsyncMock(return_value="1-1")
    mock_publisher.close = AsyncMock()

    with patch("src.api.main.StreamPublisher", return_value=mock_publisher):
        app.dependency_overrides[require_user_id] = lambda: None
        with TestClient(app, raise_server_exceptions=False) as c:
            c.app_mock_publisher = mock_publisher
            yield c
        app.dependency_overrides.clear()
