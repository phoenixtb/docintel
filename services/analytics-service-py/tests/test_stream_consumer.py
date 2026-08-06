"""
Tests for the A7 analytics.query Redis Streams consumer.

Uses fakeredis for genuine XADD/XREADGROUP/XACK semantics (same pattern as
lib/docintel-common/tests/test_messaging.py), and a mocked ClickHouse client
so no live ClickHouse is required.
"""

import asyncio
from unittest.mock import MagicMock, patch

import fakeredis.aioredis as fakeredis
import pytest
from docintel_common.messaging import TOPIC_ANALYTICS_QUERY, RedisStreamBus

from src.config import Settings
from src.stream_consumer import AnalyticsStreamConsumer, _CONSUMER_GROUP


def _settings(**overrides) -> Settings:
    return Settings(
        analytics_consumer_batch_size=overrides.pop("analytics_consumer_batch_size", 3),
        analytics_consumer_flush_interval_s=overrides.pop("analytics_consumer_flush_interval_s", 60.0),
        **overrides,
    )


@pytest.fixture
def fake_bus(monkeypatch):
    fake_client = fakeredis.FakeRedis(decode_responses=True)
    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "Redis", lambda **kwargs: fake_client)
    bus = RedisStreamBus(host="localhost", port=6379)
    yield bus


def _event_payload(**overrides) -> dict:
    payload = {
        "query_id": "q1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "latency_ms": 100,
        "model_used": "test-model",
        "cache_hit": False,
        "source_count": 3,
        "thinking_truncated": False,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cost_usd": 0.001,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_flush_inserts_batch_and_acks_on_success(fake_bus):
    # ensure_group uses id="$" (only new messages) — must exist before publishing
    # or the consumer group never sees these events.
    await fake_bus.ensure_group(TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP)
    await fake_bus.publish(TOPIC_ANALYTICS_QUERY, _event_payload(query_id="q1"))
    await fake_bus.publish(TOPIC_ANALYTICS_QUERY, _event_payload(query_id="q2"))
    await fake_bus.publish(TOPIC_ANALYTICS_QUERY, _event_payload(query_id="q3"))

    mock_ch = MagicMock()
    consumer = AnalyticsStreamConsumer(_settings(), bus=fake_bus)

    with patch("src.stream_consumer.get_client", return_value=mock_ch):
        run_task = asyncio.create_task(consumer.run())
        for _ in range(50):
            await asyncio.sleep(0.05)
            if consumer.batches_flushed >= 1:
                break
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)

    assert consumer.batches_flushed == 1
    assert consumer.events_flushed == 3
    mock_ch.insert.assert_called_once()
    rows = mock_ch.insert.call_args[0][1]
    assert len(rows) == 3

    pending = await fake_bus._redis.xpending_range(
        TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP, min="-", max="+", count=10
    )
    assert len(pending) == 0


@pytest.mark.asyncio
async def test_flush_leaves_messages_pending_on_insert_failure(fake_bus):
    await fake_bus.ensure_group(TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP)
    await fake_bus.publish(TOPIC_ANALYTICS_QUERY, _event_payload(query_id="q1"))
    await fake_bus.publish(TOPIC_ANALYTICS_QUERY, _event_payload(query_id="q2"))
    await fake_bus.publish(TOPIC_ANALYTICS_QUERY, _event_payload(query_id="q3"))

    mock_ch = MagicMock()
    mock_ch.insert.side_effect = RuntimeError("clickhouse down")
    consumer = AnalyticsStreamConsumer(_settings(), bus=fake_bus)

    with patch("src.stream_consumer.get_client", return_value=mock_ch):
        run_task = asyncio.create_task(consumer.run())
        for _ in range(50):
            await asyncio.sleep(0.05)
            if mock_ch.insert.call_count >= 1:
                break
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)

    assert consumer.batches_flushed == 0

    pending = await fake_bus._redis.xpending_range(
        TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP, min="-", max="+", count=10
    )
    assert len(pending) == 3  # not acked — stays for XAUTOCLAIM redelivery


@pytest.mark.asyncio
async def test_flush_triggered_by_interval_below_batch_size(fake_bus):
    """Two events below the batch_size threshold still flush once the
    periodic interval elapses."""
    await fake_bus.ensure_group(TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP)
    await fake_bus.publish(TOPIC_ANALYTICS_QUERY, _event_payload(query_id="q1"))
    await fake_bus.publish(TOPIC_ANALYTICS_QUERY, _event_payload(query_id="q2"))

    mock_ch = MagicMock()
    consumer = AnalyticsStreamConsumer(
        _settings(analytics_consumer_batch_size=200, analytics_consumer_flush_interval_s=0.1),
        bus=fake_bus,
    )

    with patch("src.stream_consumer.get_client", return_value=mock_ch):
        run_task = asyncio.create_task(consumer.run())
        for _ in range(50):
            await asyncio.sleep(0.05)
            if consumer.batches_flushed >= 1:
                break
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)

    assert consumer.batches_flushed == 1
    assert consumer.events_flushed == 2


@pytest.mark.asyncio
async def test_row_columns_and_types_match_query_events_schema(fake_bus):
    await fake_bus.ensure_group(TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP)
    await fake_bus.publish(
        TOPIC_ANALYTICS_QUERY,
        _event_payload(query_id="q1", prompt_tokens=120, completion_tokens=45, cost_usd=0.0021),
    )

    mock_ch = MagicMock()
    consumer = AnalyticsStreamConsumer(_settings(analytics_consumer_batch_size=1), bus=fake_bus)

    with patch("src.stream_consumer.get_client", return_value=mock_ch):
        run_task = asyncio.create_task(consumer.run())
        for _ in range(50):
            await asyncio.sleep(0.05)
            if consumer.batches_flushed >= 1:
                break
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)

    _, kwargs = mock_ch.insert.call_args
    columns = kwargs["column_names"]
    rows = mock_ch.insert.call_args[0][1]
    row = rows[0]
    assert row[columns.index("query_id")] == "q1"
    assert row[columns.index("prompt_tokens")] == 120
    assert row[columns.index("completion_tokens")] == 45
    assert row[columns.index("cost_usd")] == pytest.approx(0.0021)
