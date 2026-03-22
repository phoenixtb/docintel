"""
Unit tests for the ingestion-service Redis stream worker.

Tests _handle_message directly with fully mocked bus, MinIO adapter,
ingestion pipeline, and persistence layer — no real Redis or Docker required.
"""

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.stream_worker import _handle_message, _CONSUMER_GROUP
from docintel_common.messaging import TOPIC_INGESTION_COMPLETE, TOPIC_DOCUMENTS_READY

_DOCUMENT_ID = str(uuid.uuid4())
_TENANT_ID = "test-tenant"

READY_PAYLOAD = {
    "documentId": _DOCUMENT_ID,
    "tenantId": _TENANT_ID,
    "bucket": f"docintel-{_TENANT_ID}",
    "objectPath": f"docs/abc/original.txt",
    "filename": "test.txt",
    "domainHint": "auto",
    "metadata": {},
}

MOCK_RESULT = {
    "chunk_count": 3,
    "domain": "contracts",
    "chunks": [
        {
            "chunk_id": str(uuid.uuid4()),
            "content": f"Chunk {i}",
            "chunk_index": i,
            "start_char": i * 100,
            "end_char": (i + 1) * 100,
            "token_count": 10,
            "metadata": {},
        }
        for i in range(3)
    ],
    "embedded_count": 3,
    "collection": f"documents_{_TENANT_ID}",
}


@pytest.fixture
def bus():
    b = AsyncMock()
    return b


@pytest.fixture
def mock_paths(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("sample content")
    return [f]


@pytest.mark.asyncio
async def test_handle_message_success_publishes_completed_and_acks(bus, mock_paths):
    settings = MagicMock()

    with (
        patch("src.stream_worker.MinIOAdapter") as mock_adapter_cls,
        patch("src.stream_worker.run_ingestion", return_value=MOCK_RESULT),
        patch("src.stream_worker.persist_chunks") as mock_persist,
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "1-1", READY_PAYLOAD.copy(), settings)

    bus.publish.assert_called_once()
    topic_arg, payload_arg = bus.publish.call_args.args
    assert topic_arg == TOPIC_INGESTION_COMPLETE
    assert payload_arg["status"] == "COMPLETED"
    assert payload_arg["chunkCount"] == 3
    assert payload_arg["documentId"] == _DOCUMENT_ID
    assert payload_arg["tenantId"] == _TENANT_ID

    bus.ack.assert_called_once_with(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, "1-1")
    mock_persist.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_persists_correct_chunk_count(bus, mock_paths):
    settings = MagicMock()

    with (
        patch("src.stream_worker.MinIOAdapter") as mock_adapter_cls,
        patch("src.stream_worker.run_ingestion", return_value=MOCK_RESULT),
        patch("src.stream_worker.persist_chunks") as mock_persist,
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "2-1", READY_PAYLOAD.copy(), settings)

    chunk_records = mock_persist.call_args.args[0]
    assert len(chunk_records) == 3
    assert chunk_records[0].document_id == _DOCUMENT_ID
    assert chunk_records[0].tenant_id == _TENANT_ID


@pytest.mark.asyncio
async def test_handle_message_publishes_failed_on_minio_error(bus):
    settings = MagicMock()

    with patch("src.stream_worker.MinIOAdapter") as mock_adapter_cls:
        mock_adapter_cls.return_value.fetch = AsyncMock(
            side_effect=RuntimeError("MinIO unreachable")
        )

        await _handle_message(bus, "3-1", READY_PAYLOAD.copy(), settings)

    bus.publish.assert_called_once()
    topic_arg, payload_arg = bus.publish.call_args.args
    assert topic_arg == TOPIC_INGESTION_COMPLETE
    assert payload_arg["status"] == "FAILED"
    assert payload_arg["chunkCount"] == 0
    assert "MinIO unreachable" in payload_arg["errorMessage"]

    bus.ack.assert_called_once_with(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, "3-1")


@pytest.mark.asyncio
async def test_handle_message_publishes_failed_on_pipeline_error(bus, mock_paths):
    settings = MagicMock()

    with (
        patch("src.stream_worker.MinIOAdapter") as mock_adapter_cls,
        patch("src.stream_worker.run_ingestion", side_effect=RuntimeError("GPU OOM")),
        patch("src.stream_worker.persist_chunks"),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "4-1", READY_PAYLOAD.copy(), settings)

    _, payload_arg = bus.publish.call_args.args
    assert payload_arg["status"] == "FAILED"
    assert "GPU OOM" in payload_arg["errorMessage"]

    bus.ack.assert_called_once_with(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, "4-1")


@pytest.mark.asyncio
async def test_handle_message_uses_camelcase_payload_keys(bus, mock_paths):
    """Payload from document-service uses camelCase keys."""
    settings = MagicMock()
    camel_payload = {
        "documentId": _DOCUMENT_ID,
        "tenantId": _TENANT_ID,
        "bucket": f"docintel-{_TENANT_ID}",
        "objectPath": "docs/camel/original.txt",
        "filename": "camel.txt",
        "domainHint": "hr_policy",
        "metadata": {"source": "stream"},
    }

    with (
        patch("src.stream_worker.MinIOAdapter") as mock_adapter_cls,
        patch("src.stream_worker.run_ingestion", return_value=MOCK_RESULT) as mock_run,
        patch("src.stream_worker.persist_chunks"),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "5-1", camel_payload, settings)

    _, payload_arg = bus.publish.call_args.args
    assert payload_arg["status"] == "COMPLETED"
    assert payload_arg["documentId"] == _DOCUMENT_ID
