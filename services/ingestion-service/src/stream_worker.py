"""
Redis Streams consumer worker for the ingestion-service.

Listens on [documents.ready] and runs the full Haystack ingestion pipeline for
each event. On completion (or failure) publishes an [ingestion.complete] event so
document-service can update the document record without needing a direct DB
connection from ingestion-service.

Consumer group: ingestion-service
Stream:         documents.ready  (published by document-service FilesAvailableConsumer
                                  or DocumentService.processDocument after Phase 1)
Publishes to:   ingestion.complete (consumed by document-service IngestionCompleteConsumer)

Concurrency model (Phase 4):
  - asyncio.Semaphore(DOCLING_MAX_WORKERS): up to N documents processed concurrently
  - concurrent.futures.ThreadPoolExecutor(N): each worker runs in a thread
    (page-sharding already bounds per-shard memory usage)
  - Per-tenant round-robin dispatch: prevents one tenant's large PDF from starving others
  - XAUTOCLAIM recovery: idle PEL messages (worker crashed) are reclaimed and retried
"""

import asyncio
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from minio.error import S3Error

from docintel_common.messaging import (
    RedisStreamBus,
    TOPIC_DOCUMENTS_READY,
    TOPIC_INGESTION_COMPLETE,
)
from docintel_common.security import DocumentACL

from .adapters import MinIOAdapter
from .config import Settings
from .document_client import ChunkPayload, DocumentServiceClient
from .pipeline import run_ingestion

logger = logging.getLogger(__name__)

_CONSUMER_GROUP = "ingestion-service"
_CONSUMER_NAME  = "ingestion-service-1"
_MAX_DELIVERY_COUNT = 3   # retries before DLQ
_CLAIM_IDLE_MS      = 300_000   # 5 min idle → reclaim


async def run_stream_worker(settings: Settings) -> None:
    """
    Infinite loop consuming documents.ready events with bounded concurrency.

    Designed to be launched as an asyncio Task during lifespan and cancelled on
    shutdown. Reconnects automatically on transient Redis failures.
    """
    bus = RedisStreamBus(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
    )

    await bus.ensure_group(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP)
    logger.info(
        "Stream worker started — consuming '%s' as group '%s' (max_workers=%d)",
        TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, settings.docling_max_workers,
    )

    semaphore    = asyncio.Semaphore(settings.docling_max_workers)
    process_pool = ThreadPoolExecutor(max_workers=settings.docling_max_workers)
    pending_tasks: set[asyncio.Task] = set()

    # Per-tenant pending queue for round-robin fairness
    tenant_queues: dict[str, list[tuple[str, dict]]] = {}

    def _dispatch_queued() -> None:
        """Dispatch all messages currently sitting in tenant_queues (round-robin)."""
        for t in list(tenant_queues.keys()):
            if tenant_queues[t]:
                mid, pld = tenant_queues[t].pop(0)
                if not tenant_queues[t]:
                    del tenant_queues[t]
                # Fire-and-forget: semaphore.acquire is non-blocking if slot available,
                # otherwise the task will await it internally.
                task = asyncio.create_task(
                    _handle_message_with_semaphore(bus, mid, pld, settings, process_pool, semaphore)
                )
                pending_tasks.add(task)
                task.add_done_callback(pending_tasks.discard)

    async def _claim_and_requeue() -> None:
        """Background task: reclaim idle PEL messages, inject into tenant_queues, and dispatch."""
        while True:
            try:
                await asyncio.sleep(60)
                claimed = await bus.claim_idle(
                    TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, _CONSUMER_NAME,
                    min_idle_ms=_CLAIM_IDLE_MS,
                )
                for msg_id, payload in claimed:
                    tenant = payload.get("tenantId") or payload.get("tenant_id", "default")
                    if tenant not in tenant_queues:
                        tenant_queues[tenant] = []
                    tenant_queues[tenant].append((msg_id, payload))
                    logger.info(
                        "XAUTOCLAIM: reclaimed idle message id=%s tenant=%s", msg_id, tenant
                    )
                if claimed:
                    # Dispatch claimed messages immediately — don't wait for next XREADGROUP tick
                    _dispatch_queued()
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.warning("XAUTOCLAIM loop error: %s", e)

    claim_task = asyncio.create_task(_claim_and_requeue())

    try:
        async for msg_id, payload in bus.consume(
            TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, _CONSUMER_NAME,
            batch_size=settings.docling_max_workers * 2,
        ):
            tenant = payload.get("tenantId") or payload.get("tenant_id", "default")
            if tenant not in tenant_queues:
                tenant_queues[tenant] = []
            tenant_queues[tenant].append((msg_id, payload))
            _dispatch_queued()

    except asyncio.CancelledError:
        logger.info("Stream worker shutting down — waiting for in-flight tasks")
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
    finally:
        claim_task.cancel()
        process_pool.shutdown(wait=False, cancel_futures=True)
        await bus.close()


async def _handle_message_with_semaphore(
    bus: RedisStreamBus,
    msg_id: str,
    payload: dict,
    settings: Settings,
    process_pool: ThreadPoolExecutor,
    semaphore: asyncio.Semaphore,
) -> None:
    await semaphore.acquire()
    try:
        await _handle_message(bus, msg_id, payload, settings, process_pool)
    finally:
        semaphore.release()


async def _handle_message(
    bus: RedisStreamBus,
    msg_id: str,
    payload: dict,
    settings: Settings,
    process_pool: ThreadPoolExecutor,
) -> None:
    """Process one documents.ready event end-to-end in a thread pool worker."""
    document_id = payload.get("documentId") or payload.get("document_id", "")
    tenant_id   = payload.get("tenantId")   or payload.get("tenant_id", "default")
    bucket      = payload.get("bucket", f"docintel-{tenant_id}")
    object_path = payload.get("objectPath") or payload.get("object_path", "")
    filename    = payload.get("filename", "document")
    domain_hint = payload.get("domainHint") or payload.get("domain_hint", "auto")
    metadata    = payload.get("metadata", {})

    logger.info(
        "Processing documents.ready: document_id=%s tenant=%s file=%s msg_id=%s",
        document_id, tenant_id, filename, msg_id,
    )

    # Check delivery count for max-retry enforcement
    delivery_count = await bus.delivery_count(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, msg_id)
    if delivery_count > _MAX_DELIVERY_COUNT:
        logger.error(
            "Message id=%s exceeded max retries (%d). Moving to DLQ. document_id=%s",
            msg_id, _MAX_DELIVERY_COUNT, document_id,
        )
        await _publish_complete(bus, document_id, tenant_id, 0, "general", "FAILED",
                                f"Exceeded max delivery count ({_MAX_DELIVERY_COUNT})")
        await bus.ack(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, msg_id)
        return

    adapter    = MinIOAdapter()
    tmp_paths: list[Path] = []

    try:
        try:
            tmp_paths = await adapter.fetch({
                "bucket":      bucket,
                "object_path": object_path,
                "filename":    filename,
            })
        except S3Error as s3e:
            if s3e.code == "NoSuchKey":
                logger.warning(
                    "File not found in MinIO (terminal): document_id=%s bucket=%s path=%s — acking and skipping",
                    document_id, bucket, object_path,
                )
                await _publish_complete(bus, document_id, tenant_id, 0, "general", "FAILED",
                                        f"Source file missing from object store: {object_path}")
                await bus.ack(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, msg_id)
                return
            raise

        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            process_pool,
            _ingestion_worker,
            tmp_paths,
            document_id,
            tenant_id,
            filename,
            domain_hint,
            metadata,
        )

        # For text/single-shard path: persist chunks via bulk endpoint
        # For PDF sharded path: chunks already persisted shard-by-shard (result["chunks"] == [])
        raw_chunks = result.get("chunks", [])
        if raw_chunks:
            chunk_payloads = [
                ChunkPayload(
                    chunk_id    = c["chunk_id"],
                    chunk_index = c["chunk_index"],
                    content     = c["content"],
                    start_char  = c["start_char"],
                    end_char    = c["end_char"],
                    token_count = c["token_count"],
                    metadata    = c["metadata"],
                )
                for c in raw_chunks
            ]
            doc_client = DocumentServiceClient()
            await loop.run_in_executor(
                None, doc_client.persist_chunks, document_id, tenant_id, chunk_payloads
            )

        await _publish_complete(
            bus, document_id, tenant_id,
            result["chunk_count"], result["domain"], "COMPLETED",
        )

        logger.info(
            "Ingestion complete (stream): document_id=%s chunks=%d domain=%s",
            document_id, result["chunk_count"], result["domain"],
        )

        # Only ack on confirmed terminal success
        await bus.ack(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, msg_id)

    except Exception as e:
        logger.exception("Stream ingestion failed for document %s", document_id)
        await _publish_complete(bus, document_id, tenant_id, 0, "general", "FAILED", str(e))
        # Do NOT ack — XAUTOCLAIM will redeliver for retry up to _MAX_DELIVERY_COUNT
        # After max retries the message is acked and moved to DLQ above.
    finally:
        for p in tmp_paths:
            try:
                shutil.rmtree(p.parent, ignore_errors=True)
            except Exception:
                pass


def _ingestion_worker(
    tmp_paths: list[Path],
    document_id: str,
    tenant_id: str,
    filename: str,
    domain_hint: str,
    metadata: dict,
) -> dict:
    """Runs run_ingestion in a thread pool worker."""
    return run_ingestion(
        file_paths  = tmp_paths,
        document_id = document_id,
        tenant_id   = tenant_id,
        filename    = filename,
        domain_hint = domain_hint,
        extra_meta  = metadata or None,
        settings    = None,
        acl         = DocumentACL(),
    )


async def _publish_complete(
    bus: RedisStreamBus,
    document_id: str,
    tenant_id: str,
    chunk_count: int,
    domain: str,
    status: str,
    error_message: str | None = None,
) -> None:
    payload: dict = {
        "documentId": document_id,
        "tenantId":   tenant_id,
        "chunkCount": chunk_count,
        "domain":     domain,
        "status":     status,
    }
    if error_message:
        payload["errorMessage"] = error_message
    await bus.publish(TOPIC_INGESTION_COMPLETE, payload)
