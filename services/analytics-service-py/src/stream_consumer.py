"""
Redis Streams consumer for query telemetry (A7).

Consumes [analytics.query] (published by rag-service — see
docintel_common.messaging.TOPIC_ANALYTICS_QUERY) and batches inserts into
ClickHouse, replacing the old row-per-request HTTP POST path. Reference
pattern: services/ingestion-service/src/stream_worker.py.

Semantics: at-least-once. A crash between a successful ClickHouse insert
and the XACK redelivers that batch on restart (duplicate rows possible —
acceptable for analytics; query_id is available for dedup at query time if
ever needed). Idle PEL entries (worker crashed mid-batch) are reclaimed via
XAUTOCLAIM, same as ingestion-service.

Design note: RedisStreamBus.consume() only yields on an actual message —
it never yields a "nothing arrived" heartbeat during an idle XREADGROUP
block. So the "flush every T seconds" rule can't be a check inside the
consume loop's body (it would never run during a quiet period); it runs on
an independent periodic task instead, guarded by a lock shared with the
consume loop's buffer append.
"""

import asyncio
import logging

from docintel_common.messaging import RedisStreamBus, TOPIC_ANALYTICS_QUERY

from .config import Settings
from .db import get_client
from .metrics import (
    ANALYTICS_BATCH_FLUSH_TOTAL,
    ANALYTICS_EVENTS_FLUSHED_TOTAL,
    ANALYTICS_STREAM_LAG,
)

logger = logging.getLogger(__name__)

_CONSUMER_GROUP = "analytics-service"
_CONSUMER_NAME = "analytics-service-1"
_LAG_SAMPLE_INTERVAL_S = 15

_COLUMNS = [
    "query_id", "tenant_id", "user_id", "latency_ms", "model_used",
    "cache_hit", "source_count", "thinking_truncated",
    "prompt_tokens", "completion_tokens", "cost_usd",
]


def _row_from_payload(payload: dict) -> list:
    return [
        str(payload.get("query_id", "")),
        str(payload.get("tenant_id", "")),
        str(payload.get("user_id", "")),
        int(payload.get("latency_ms", 0) or 0),
        str(payload.get("model_used", "")),
        bool(payload.get("cache_hit", False)),
        int(payload.get("source_count", 0) or 0),
        bool(payload.get("thinking_truncated", False)),
        int(payload.get("prompt_tokens", 0) or 0),
        int(payload.get("completion_tokens", 0) or 0),
        float(payload.get("cost_usd", 0.0) or 0.0),
    ]


class AnalyticsStreamConsumer:
    """
    Background task: buffers analytics.query events and flushes them to
    ClickHouse as one batched insert every `batch_size` events or
    `flush_interval_s` seconds, whichever comes first.
    """

    def __init__(self, settings: Settings, bus: RedisStreamBus | None = None):
        self._settings = settings
        self._bus = bus or RedisStreamBus(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
        )
        self._buffer: list[tuple[str, dict]] = []
        self._lock = asyncio.Lock()
        self.batches_flushed = 0
        self.events_flushed = 0

    async def run(self) -> None:
        await self._bus.ensure_group(TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP)
        logger.info(
            "Analytics stream consumer started — consuming '%s' as group '%s' "
            "(batch_size=%d, flush_interval_s=%.1f)",
            TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP,
            self._settings.analytics_consumer_batch_size,
            self._settings.analytics_consumer_flush_interval_s,
        )

        background = [
            asyncio.create_task(self._periodic_flush_loop()),
            asyncio.create_task(self._claim_idle_loop()),
            asyncio.create_task(self._lag_sampler_loop()),
        ]
        try:
            async for msg_id, payload in self._bus.consume(
                TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP, _CONSUMER_NAME,
                block_ms=2000,
                batch_size=self._settings.analytics_consumer_batch_size,
            ):
                async with self._lock:
                    self._buffer.append((msg_id, payload))
                    full = len(self._buffer) >= self._settings.analytics_consumer_batch_size
                if full:
                    await self._flush()
        except asyncio.CancelledError:
            logger.info("Analytics stream consumer shutting down — flushing remaining buffer")
            await self._flush()
            raise
        finally:
            for t in background:
                t.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await self._bus.close()

    async def _periodic_flush_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._settings.analytics_consumer_flush_interval_s)
                await self._flush()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Analytics periodic flush error: %s", e)

    async def _claim_idle_loop(self) -> None:
        """Reclaim PEL entries left by a consumer that crashed mid-batch."""
        while True:
            try:
                await asyncio.sleep(60)
                claimed = await self._bus.claim_idle(
                    TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP, _CONSUMER_NAME,
                    min_idle_ms=self._settings.analytics_consumer_claim_idle_ms,
                )
                if not claimed:
                    continue
                logger.info("XAUTOCLAIM reclaimed %d idle analytics event(s)", len(claimed))
                async with self._lock:
                    self._buffer.extend(claimed)
                    full = len(self._buffer) >= self._settings.analytics_consumer_batch_size
                if full:
                    await self._flush()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Analytics XAUTOCLAIM loop error: %s", e)

    async def _lag_sampler_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(_LAG_SAMPLE_INTERVAL_S)
                lag = await self._bus.group_lag(TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP)
                ANALYTICS_STREAM_LAG.labels(topic=TOPIC_ANALYTICS_QUERY, group=_CONSUMER_GROUP).set(lag)
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.debug("Analytics lag sampler error: %s", e)

    async def _flush(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            batch = self._buffer
            self._buffer = []

        msg_ids = [mid for mid, _ in batch]
        rows = [_row_from_payload(payload) for _, payload in batch]

        try:
            await asyncio.to_thread(self._insert_rows, rows)
        except Exception as e:
            # Do NOT ack — messages stay in the PEL. XAUTOCLAIM redelivers
            # them after the idle threshold (this consumer restarting also
            # naturally leaves them for claim_idle to pick back up).
            logger.error(
                "Batch insert of %d analytics event(s) failed — will retry via redelivery: %s",
                len(rows), e,
            )
            return

        for mid in msg_ids:
            await self._bus.ack(TOPIC_ANALYTICS_QUERY, _CONSUMER_GROUP, mid)

        self.batches_flushed += 1
        self.events_flushed += len(rows)
        ANALYTICS_BATCH_FLUSH_TOTAL.labels(topic=TOPIC_ANALYTICS_QUERY).inc()
        ANALYTICS_EVENTS_FLUSHED_TOTAL.labels(topic=TOPIC_ANALYTICS_QUERY).inc(len(rows))
        logger.info(
            "Flushed %d analytics event(s) to ClickHouse (batch #%d, total events %d)",
            len(rows), self.batches_flushed, self.events_flushed,
        )

    def _insert_rows(self, rows: list[list]) -> None:
        client = get_client(self._settings)
        client.insert(
            f"{self._settings.clickhouse_database}.query_events",
            rows,
            column_names=_COLUMNS,
        )
