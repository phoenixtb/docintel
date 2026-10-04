"""
Unit tests for the ingestion-service Redis stream worker.

Tests _handle_message directly with a mocked bus, object-store adapter,
ingestion pipeline and persistence client — no real Redis or Docker required.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docintel_common.messaging import TOPIC_DOCUMENTS_READY, TOPIC_INGESTION_COMPLETE
from docintel_common.object_store import ObjectNotFoundError, ObjectStoreError

from src.stream_worker import _CONSUMER_GROUP, _handle_message

_DOCUMENT_ID = str(uuid.uuid4())
_TENANT_ID = "test-tenant"

READY_PAYLOAD = {
    "documentId": _DOCUMENT_ID,
    "tenantId": _TENANT_ID,
    "bucket": f"docintel-{_TENANT_ID}",
    "objectPath": "docs/abc/original.txt",
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
    b.delivery_count = AsyncMock(return_value=1)
    return b


@pytest.fixture
def pool():
    executor = ThreadPoolExecutor(max_workers=1)
    yield executor
    executor.shutdown(wait=True)


@pytest.fixture
def mock_paths(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("sample content")
    return [f]


@pytest.mark.asyncio
async def test_handle_message_success_publishes_completed_and_acks(bus, pool, mock_paths):
    mock_doc_client = MagicMock()

    with (
        patch("src.stream_worker.ObjectStoreAdapter") as mock_adapter_cls,
        patch("src.stream_worker.default_object_store"),
        patch("src.stream_worker.run_ingestion", return_value=MOCK_RESULT),
        patch("src.stream_worker.DocumentServiceClient", return_value=mock_doc_client),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "1-1", READY_PAYLOAD.copy(), MagicMock(), pool)

    bus.publish.assert_called_once()
    topic_arg, payload_arg = bus.publish.call_args.args
    assert topic_arg == TOPIC_INGESTION_COMPLETE
    assert payload_arg["status"] == "COMPLETED"
    assert payload_arg["chunkCount"] == 3
    assert payload_arg["documentId"] == _DOCUMENT_ID
    assert payload_arg["tenantId"] == _TENANT_ID

    bus.ack.assert_called_once_with(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, "1-1")
    mock_doc_client.persist_chunks.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_fetches_bucket_and_object_path_from_payload(bus, pool, mock_paths):
    with (
        patch("src.stream_worker.ObjectStoreAdapter") as mock_adapter_cls,
        patch("src.stream_worker.default_object_store"),
        patch("src.stream_worker.run_ingestion", return_value=MOCK_RESULT),
        patch("src.stream_worker.DocumentServiceClient", return_value=MagicMock()),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "1-2", READY_PAYLOAD.copy(), MagicMock(), pool)

    mock_adapter_cls.return_value.fetch.assert_awaited_once_with(
        {
            "bucket": f"docintel-{_TENANT_ID}",
            "object_path": "docs/abc/original.txt",
            "filename": "test.txt",
        }
    )


@pytest.mark.asyncio
async def test_handle_message_persists_correct_chunk_count(bus, pool, mock_paths):
    mock_doc_client = MagicMock()

    with (
        patch("src.stream_worker.ObjectStoreAdapter") as mock_adapter_cls,
        patch("src.stream_worker.default_object_store"),
        patch("src.stream_worker.run_ingestion", return_value=MOCK_RESULT),
        patch("src.stream_worker.DocumentServiceClient", return_value=mock_doc_client),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "2-1", READY_PAYLOAD.copy(), MagicMock(), pool)

    # persist_chunks is called as (document_id, tenant_id, chunk_payloads)
    doc_id_arg, tenant_id_arg, chunk_payloads_arg = mock_doc_client.persist_chunks.call_args.args
    assert doc_id_arg == _DOCUMENT_ID
    assert tenant_id_arg == _TENANT_ID
    assert len(chunk_payloads_arg) == 3


@pytest.mark.asyncio
async def test_missing_source_object_fails_document_and_acks_without_retry(bus, pool):
    with (
        patch("src.stream_worker.ObjectStoreAdapter") as mock_adapter_cls,
        patch("src.stream_worker.default_object_store"),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(
            side_effect=ObjectNotFoundError("get docintel-test-tenant/docs/abc: NoSuchKey")
        )

        await _handle_message(bus, "3-1", READY_PAYLOAD.copy(), MagicMock(), pool)

    _, payload_arg = bus.publish.call_args.args
    assert payload_arg["status"] == "FAILED"
    assert payload_arg["chunkCount"] == 0
    assert "missing from object store" in payload_arg["errorMessage"]
    bus.ack.assert_called_once_with(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, "3-1")


@pytest.mark.asyncio
async def test_object_store_outage_fails_document_but_leaves_message_for_redelivery(bus, pool):
    with (
        patch("src.stream_worker.ObjectStoreAdapter") as mock_adapter_cls,
        patch("src.stream_worker.default_object_store"),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(
            side_effect=ObjectStoreError("get docintel-test-tenant/docs/abc failed: InternalError")
        )

        await _handle_message(bus, "3-2", READY_PAYLOAD.copy(), MagicMock(), pool)

    _, payload_arg = bus.publish.call_args.args
    assert payload_arg["status"] == "FAILED"
    assert "InternalError" in payload_arg["errorMessage"]
    bus.ack.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_publishes_failed_on_pipeline_error(bus, pool, mock_paths):
    with (
        patch("src.stream_worker.ObjectStoreAdapter") as mock_adapter_cls,
        patch("src.stream_worker.default_object_store"),
        patch("src.stream_worker.run_ingestion", side_effect=RuntimeError("GPU OOM")),
        patch("src.stream_worker.DocumentServiceClient", return_value=MagicMock()),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "4-1", READY_PAYLOAD.copy(), MagicMock(), pool)

    _, payload_arg = bus.publish.call_args.args
    assert payload_arg["status"] == "FAILED"
    assert "GPU OOM" in payload_arg["errorMessage"]
    # Transient failure: no ack, so XAUTOCLAIM redelivers up to the retry limit.
    bus.ack.assert_not_called()


@pytest.mark.asyncio
async def test_message_over_retry_limit_is_failed_and_acked_without_fetching(bus, pool):
    bus.delivery_count = AsyncMock(return_value=4)

    with (
        patch("src.stream_worker.ObjectStoreAdapter") as mock_adapter_cls,
        patch("src.stream_worker.default_object_store"),
    ):
        await _handle_message(bus, "6-1", READY_PAYLOAD.copy(), MagicMock(), pool)

    mock_adapter_cls.assert_not_called()
    _, payload_arg = bus.publish.call_args.args
    assert payload_arg["status"] == "FAILED"
    bus.ack.assert_called_once_with(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, "6-1")


@pytest.mark.asyncio
async def test_handle_message_uses_camelcase_payload_keys(bus, pool, mock_paths):
    """Payload from document-service uses camelCase keys."""
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
        patch("src.stream_worker.ObjectStoreAdapter") as mock_adapter_cls,
        patch("src.stream_worker.default_object_store"),
        patch("src.stream_worker.run_ingestion", return_value=MOCK_RESULT) as mock_run,
        patch("src.stream_worker.DocumentServiceClient", return_value=MagicMock()),
    ):
        mock_adapter_cls.return_value.fetch = AsyncMock(return_value=mock_paths)

        await _handle_message(bus, "5-1", camel_payload, MagicMock(), pool)

    _, payload_arg = bus.publish.call_args.args
    assert payload_arg["status"] == "COMPLETED"
    assert payload_arg["documentId"] == _DOCUMENT_ID
    assert mock_run.call_args.kwargs["domain_hint"] == "hr_policy"
