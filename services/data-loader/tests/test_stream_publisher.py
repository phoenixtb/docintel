"""
Unit tests for StreamPublisher using fakeredis in-memory backend.

fakeredis supports full Redis Streams semantics (XADD, XGROUP CREATE, XREAD)
without a real server — giving genuine coverage of the publisher logic at
zero infrastructure cost.
"""

import json

import fakeredis.aioredis as fakeredis
import pytest

from src.stream_publisher import StreamPublisher, TOPIC_FILES_AVAILABLE, _DOCUMENT_SERVICE_GROUP


@pytest.fixture
async def publisher(monkeypatch):
    """StreamPublisher backed by a fakeredis instance."""
    fake_client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "src.stream_publisher.aioredis.Redis",
        lambda **kwargs: fake_client,
    )
    pub = StreamPublisher(host="localhost", port=6379)
    yield pub, fake_client
    await pub.close()


@pytest.mark.asyncio
async def test_ensure_consumer_group_creates_stream_and_group(publisher):
    pub, redis = publisher
    await pub.ensure_consumer_group()

    groups = await redis.xinfo_groups(TOPIC_FILES_AVAILABLE)
    group_names = [g["name"] for g in groups]
    assert _DOCUMENT_SERVICE_GROUP in group_names


@pytest.mark.asyncio
async def test_ensure_consumer_group_is_idempotent(publisher):
    pub, redis = publisher
    await pub.ensure_consumer_group()
    await pub.ensure_consumer_group()  # second call must not raise

    groups = await redis.xinfo_groups(TOPIC_FILES_AVAILABLE)
    assert len(groups) == 1


@pytest.mark.asyncio
async def test_publish_file_available_returns_message_id(publisher):
    pub, redis = publisher
    await pub.ensure_consumer_group()

    payload = {
        "minioPath": "docs/abc/original.txt",
        "contentHash": "a" * 64,
        "tenantId": "test-tenant",
        "filename": "test.txt",
        "contentType": "text/plain",
        "fileSize": 100,
        "dataSourceId": None,
        "domainHint": "auto",
        "metadata": {},
    }
    msg_id = await pub.publish_file_available(payload)
    assert msg_id is not None
    assert "-" in str(msg_id)  # Redis stream IDs are "timestamp-seq"


@pytest.mark.asyncio
async def test_publish_file_available_message_is_readable(publisher):
    pub, redis = publisher
    await pub.ensure_consumer_group()

    payload = {
        "minioPath": "docs/def/original.txt",
        "contentHash": "b" * 64,
        "tenantId": "readable-tenant",
        "filename": "readable.txt",
        "contentType": "text/plain",
        "fileSize": 200,
        "dataSourceId": None,
        "domainHint": "contracts",
        "metadata": {"source": "test"},
    }
    await pub.publish_file_available(payload)

    messages = await redis.xread({TOPIC_FILES_AVAILABLE: "0"}, count=10)
    assert len(messages) == 1
    stream_name, entries = messages[0]
    msg_payload = json.loads(entries[0][1]["payload"])
    assert msg_payload["tenantId"] == "readable-tenant"
    assert msg_payload["filename"] == "readable.txt"
    assert msg_payload["domainHint"] == "contracts"


@pytest.mark.asyncio
async def test_publish_multiple_messages_are_ordered(publisher):
    pub, redis = publisher
    await pub.ensure_consumer_group()

    for i in range(3):
        await pub.publish_file_available(
            {
                "minioPath": f"docs/{i}/original.txt",
                "contentHash": str(i) * 64,
                "tenantId": "order-tenant",
                "filename": f"doc_{i}.txt",
                "contentType": "text/plain",
                "fileSize": i * 100,
            }
        )

    messages = await redis.xread({TOPIC_FILES_AVAILABLE: "0"}, count=10)
    _, entries = messages[0]
    assert len(entries) == 3
    filenames = [json.loads(e[1]["payload"])["filename"] for e in entries]
    assert filenames == ["doc_0.txt", "doc_1.txt", "doc_2.txt"]
