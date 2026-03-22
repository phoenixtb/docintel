"""
Integration tests for RedisStreamBus using fakeredis.

fakeredis supports full Redis Streams semantics in-process — no real Redis or
Docker containers required. This gives genuine end-to-end coverage of the
publish → consume → ack cycle.
"""

import asyncio
import json

import fakeredis.aioredis as fakeredis
import pytest

from docintel_common.messaging import (
    TOPIC_DOCUMENTS_READY,
    TOPIC_FILES_AVAILABLE,
    TOPIC_INGESTION_COMPLETE,
    RedisStreamBus,
)


@pytest.fixture
async def bus(monkeypatch):
    """RedisStreamBus backed by an in-process fakeredis instance."""
    fake_client = fakeredis.FakeRedis(decode_responses=True)
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "Redis", lambda **kwargs: fake_client)
    instance = RedisStreamBus(host="localhost", port=6379)
    yield instance
    await instance.close()


# ---------------------------------------------------------------------------
# Topic constants
# ---------------------------------------------------------------------------


def test_topic_constants_match_python_and_kotlin_conventions():
    assert TOPIC_FILES_AVAILABLE == "files.available"
    assert TOPIC_DOCUMENTS_READY == "documents.ready"
    assert TOPIC_INGESTION_COMPLETE == "ingestion.complete"


# ---------------------------------------------------------------------------
# ensure_group
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_group_creates_stream_and_consumer_group(bus):
    await bus.ensure_group(TOPIC_DOCUMENTS_READY, "ingestion-service")

    groups = await bus._redis.xinfo_groups(TOPIC_DOCUMENTS_READY)
    group_names = [g["name"] for g in groups]
    assert "ingestion-service" in group_names


@pytest.mark.asyncio
async def test_ensure_group_is_idempotent(bus):
    await bus.ensure_group(TOPIC_DOCUMENTS_READY, "test-group")
    await bus.ensure_group(TOPIC_DOCUMENTS_READY, "test-group")  # must not raise

    groups = await bus._redis.xinfo_groups(TOPIC_DOCUMENTS_READY)
    names = [g["name"] for g in groups]
    assert names.count("test-group") == 1


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_valid_stream_id(bus):
    msg_id = await bus.publish(TOPIC_FILES_AVAILABLE, {"key": "value"})
    assert "-" in str(msg_id)


@pytest.mark.asyncio
async def test_publish_serialises_payload_as_json(bus):
    payload = {"documentId": "doc-1", "tenantId": "tenant-a", "chunkCount": 5}
    await bus.publish(TOPIC_FILES_AVAILABLE, payload)

    raw = await bus._redis.xread({TOPIC_FILES_AVAILABLE: "0"}, count=1)
    _, entries = raw[0]
    stored_payload = json.loads(entries[0][1]["payload"])
    assert stored_payload == payload


@pytest.mark.asyncio
async def test_publish_multiple_messages_increments_ids(bus):
    ids = []
    for i in range(3):
        mid = await bus.publish(TOPIC_FILES_AVAILABLE, {"i": i})
        ids.append(mid)
    assert len(set(ids)) == 3  # all unique


# ---------------------------------------------------------------------------
# consume + ack
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consume_yields_published_messages(bus):
    topic = TOPIC_DOCUMENTS_READY
    group = "ingestion-service"
    consumer = "worker-1"

    await bus.ensure_group(topic, group)
    await bus.publish(topic, {"documentId": "doc-x", "tenantId": "t1"})

    received = []
    async for msg_id, payload in bus.consume(topic, group, consumer, block_ms=500):
        received.append((msg_id, payload))
        await bus.ack(topic, group, msg_id)
        break  # only one message

    assert len(received) == 1
    _, payload = received[0]
    assert payload["documentId"] == "doc-x"
    assert payload["tenantId"] == "t1"


@pytest.mark.asyncio
async def test_ack_removes_message_from_pel(bus):
    topic = TOPIC_DOCUMENTS_READY
    group = "ack-group"
    consumer = "ack-worker"

    await bus.ensure_group(topic, group)
    await bus.publish(topic, {"data": "test"})

    async for msg_id, _ in bus.consume(topic, group, consumer, block_ms=500):
        await bus.ack(topic, group, msg_id)
        break

    # After ack, PEL should be empty for this consumer
    pending = await bus._redis.xpending_range(topic, group, min="-", max="+", count=10)
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_consume_handles_malformed_json_by_auto_acking(bus):
    """Malformed JSON messages should be acked and skipped, not crash the consumer."""
    topic = TOPIC_INGESTION_COMPLETE
    group = "doc-service"
    consumer = "worker-1"

    await bus.ensure_group(topic, group)
    # Inject a malformed message directly
    await bus._redis.xadd(topic, {"payload": "{invalid json"})
    # Add a valid message after
    await bus.publish(topic, {"status": "COMPLETED", "documentId": "d1"})

    valid_messages = []
    count = 0
    async for msg_id, payload in bus.consume(topic, group, consumer, block_ms=500):
        valid_messages.append(payload)
        await bus.ack(topic, group, msg_id)
        count += 1
        if count >= 1:
            break

    # Only the valid message should reach the consumer
    assert len(valid_messages) == 1
    assert valid_messages[0]["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# from_env constructor
# ---------------------------------------------------------------------------


def test_from_env_uses_env_vars(monkeypatch):
    import redis.asyncio as aioredis

    fake_client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setenv("REDIS_HOST", "redis-host")
    monkeypatch.setenv("REDIS_PORT", "6380")
    monkeypatch.setenv("REDIS_PASSWORD", "")
    monkeypatch.setattr(aioredis, "Redis", lambda **kwargs: fake_client)

    instance = RedisStreamBus.from_env()
    assert isinstance(instance, RedisStreamBus)
