"""
Async message bus abstraction for DocIntel pipeline stages.

Provides a transport-agnostic interface so pipeline stages are not locked into
Redis Streams. A Kafka, Pulsar, or in-process bus can be substituted without
changing any business logic.

Usage (publisher):
    bus = RedisStreamBus.from_env()
    await bus.publish("documents.ready", {"document_id": ..., "tenant_id": ...})

Usage (consumer — background task):
    bus = RedisStreamBus.from_env()
    await bus.ensure_group("documents.ready", "ingestion-service")
    async for msg_id, payload in bus.consume("documents.ready", "ingestion-service", "worker-1"):
        await handle(payload)
        await bus.ack("documents.ready", "ingestion-service", msg_id)
"""

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Redis Stream topics — centralised here so all services import from one place
TOPIC_FILES_AVAILABLE    = "files.available"
TOPIC_DOCUMENTS_READY    = "documents.ready"
TOPIC_INGESTION_COMPLETE = "ingestion.complete"
TOPIC_DOCUMENTS_PROGRESS = "documents.progress"   # per-shard SSE progress events


class MessageBus(ABC):
    """Transport-agnostic async message bus interface."""

    @abstractmethod
    async def publish(self, topic: str, message: dict) -> str:
        """Publish a message to a topic. Returns the message ID."""
        ...

    @abstractmethod
    async def ensure_group(self, topic: str, group: str) -> None:
        """
        Ensure the consumer group exists for the topic.
        Creates the group (and the stream if needed) if absent.
        Safe to call multiple times (idempotent).
        """
        ...

    @abstractmethod
    async def consume(
        self,
        topic: str,
        group: str,
        consumer: str,
        *,
        block_ms: int = 5000,
        batch_size: int = 10,
    ) -> AsyncIterator[tuple[str, dict]]:
        """
        Async generator that yields (message_id, payload) tuples.

        Reads new messages for the consumer group. After processing each message
        the caller must call ack(). Uses blocking read with timeout so the
        consumer loop remains responsive to shutdown signals.
        """
        ...

    @abstractmethod
    async def ack(self, topic: str, group: str, message_id: str) -> None:
        """Acknowledge a message so it is removed from the PEL (pending-entry list)."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release underlying resources."""
        ...


class RedisStreamBus(MessageBus):
    """
    Redis Streams implementation of MessageBus.

    Requires redis-py >= 5.0 with async support (aioredis-compatible interface
    built into redis-py since v4.2).
    """

    def __init__(self, host: str, port: int, password: str | None = None, db: int = 0) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.Redis(
            host=host,
            port=port,
            password=password or None,
            db=db,
            decode_responses=True,
        )

    @classmethod
    def from_env(cls) -> "RedisStreamBus":
        """Construct from standard environment variables."""
        host = os.environ.get("REDIS_HOST", "redis")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        password = os.environ.get("REDIS_PASSWORD") or None
        return cls(host=host, port=port, password=password)

    async def publish(self, topic: str, message: dict) -> str:
        msg_id: str = await self._redis.xadd(topic, {"payload": json.dumps(message)})
        logger.debug("Published to %s: %s", topic, msg_id)
        return msg_id

    async def ensure_group(self, topic: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(
                name=topic,
                groupname=group,
                id="$",          # only new messages after group creation
                mkstream=True,   # create the stream if it doesn't exist
            )
            logger.info("Created consumer group '%s' on stream '%s'", group, topic)
        except Exception as e:
            if "BUSYGROUP" in str(e):
                pass   # group already exists — idempotent
            else:
                raise

    async def consume(
        self,
        topic: str,
        group: str,
        consumer: str,
        *,
        block_ms: int = 5000,
        batch_size: int = 10,
    ) -> AsyncIterator[tuple[str, dict]]:
        """
        Infinite async generator that reads from a Redis Stream consumer group.

        Yields (message_id, payload) pairs. The caller must await ack() after
        processing each message.
        """
        while True:
            try:
                entries = await self._redis.xreadgroup(
                    groupname=group,
                    consumername=consumer,
                    streams={topic: ">"},   # ">" = only undelivered messages
                    count=batch_size,
                    block=block_ms,
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("Redis consume error on %s/%s: %s — retrying", topic, group, e)
                await asyncio.sleep(2)
                continue

            if not entries:
                # block_ms elapsed with no messages → yield control and loop
                await asyncio.sleep(0)
                continue

            for _stream, messages in entries:
                for msg_id, fields in messages:
                    raw = fields.get("payload", "{}")
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.error("Malformed payload on %s id=%s: %r", topic, msg_id, raw)
                        await self.ack(topic, group, msg_id)
                        continue
                    yield msg_id, payload

    async def ack(self, topic: str, group: str, message_id: str) -> None:
        await self._redis.xack(topic, group, message_id)

    async def claim_idle(
        self,
        topic: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int = 300_000,  # 5 minutes
        batch_size: int = 10,
    ) -> list[tuple[str, dict]]:
        """
        Claim idle messages from the PEL (pending-entry list) via XAUTOCLAIM.

        Returns messages that have been pending for longer than [min_idle_ms] — these
        are candidates for retry (worker crashed without acking). The caller checks the
        delivery count and either reprocesses or moves to DLQ + acks.
        """
        try:
            result = await self._redis.xautoclaim(
                name=topic,
                groupname=group,
                consumername=consumer,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=batch_size,
            )
            # xautoclaim returns (next_start_id, entries, deleted_ids) in redis-py 5.x
            entries = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else result
            claimed: list[tuple[str, dict]] = []
            for msg_id, fields in (entries or []):
                raw = fields.get("payload", "{}")
                try:
                    payload = json.loads(raw)
                    claimed.append((msg_id, payload))
                except json.JSONDecodeError:
                    logger.error("Malformed PEL payload id=%s: %r", msg_id, raw)
                    await self.ack(topic, group, msg_id)
            return claimed
        except Exception as e:
            logger.warning("XAUTOCLAIM failed on %s/%s: %s", topic, group, e)
            return []

    async def delivery_count(self, topic: str, group: str, message_id: str) -> int:
        """Return how many times a message has been delivered (from XPENDING)."""
        try:
            entries = await self._redis.xpending_range(topic, group, min=message_id, max=message_id, count=1)
            if entries:
                return entries[0].get("times_delivered", 1)
        except Exception:
            pass
        return 1

    async def close(self) -> None:
        await self._redis.aclose()
