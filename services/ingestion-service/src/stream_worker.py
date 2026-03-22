"""
Redis Streams consumer worker for the ingestion-service.

Listens on [documents.ready] and runs the full Haystack ingestion pipeline for
each event. On completion (or failure) publishes an [ingestion.complete] event so
document-service can update the document record without needing a direct DB
connection from ingestion-service.

Consumer group: ingestion-service
Stream:         documents.ready  (published by document-service FilesAvailableConsumer)
Publishes to:   ingestion.complete (consumed by document-service IngestionCompleteConsumer)

This worker runs as an asyncio background task alongside the FastAPI server.
"""

import asyncio
import logging
import shutil
from pathlib import Path

from docintel_common.messaging import (
    RedisStreamBus,
    TOPIC_DOCUMENTS_READY,
    TOPIC_INGESTION_COMPLETE,
)
from docintel_common.security import DocumentACL

from .adapters import MinIOAdapter
from .config import Settings
from .db import ChunkRecord, persist_chunks
from .pipeline import run_ingestion

logger = logging.getLogger(__name__)

_CONSUMER_GROUP = "ingestion-service"
_CONSUMER_NAME  = "ingestion-service-1"


async def run_stream_worker(settings: Settings) -> None:
    """
    Infinite loop consuming documents.ready events.

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
        "Stream worker started — consuming '%s' as group '%s'",
        TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP,
    )

    try:
        async for msg_id, payload in bus.consume(
            TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, _CONSUMER_NAME
        ):
            await _handle_message(bus, msg_id, payload, settings)
    except asyncio.CancelledError:
        logger.info("Stream worker shutting down")
    finally:
        await bus.close()


async def _handle_message(
    bus: RedisStreamBus,
    msg_id: str,
    payload: dict,
    settings: Settings,
) -> None:
    """Process one documents.ready event end-to-end."""
    document_id = payload.get("documentId") or payload.get("document_id", "")
    tenant_id   = payload.get("tenantId")   or payload.get("tenant_id", "default")
    bucket      = payload.get("bucket", f"docintel-{tenant_id}")
    object_path = payload.get("objectPath") or payload.get("object_path", "")
    filename    = payload.get("filename", "document")
    domain_hint = payload.get("domainHint") or payload.get("domain_hint", "auto")
    metadata    = payload.get("metadata", {})

    logger.info(
        "Processing documents.ready: document_id=%s tenant=%s file=%s",
        document_id, tenant_id, filename,
    )

    adapter    = MinIOAdapter()
    tmp_paths: list[Path] = []

    try:
        tmp_paths = await adapter.fetch({
            "bucket":      bucket,
            "object_path": object_path,
            "filename":    filename,
        })

        loop   = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            run_ingestion,
            tmp_paths,
            document_id,
            tenant_id,
            filename,
            domain_hint,
            metadata,
            None,           # settings → uses get_settings() internally
            DocumentACL(),  # default ACL for stream-originated documents
        )

        chunk_records = [
            ChunkRecord(
                chunk_id   = c["chunk_id"],
                document_id = document_id,
                tenant_id  = tenant_id,
                content    = c["content"],
                chunk_index = c["chunk_index"],
                start_char = c["start_char"],
                end_char   = c["end_char"],
                token_count = c["token_count"],
                metadata   = c["metadata"],
            )
            for c in result["chunks"]
        ]
        persist_chunks(chunk_records)

        await bus.publish(TOPIC_INGESTION_COMPLETE, {
            "documentId": document_id,
            "tenantId":   tenant_id,
            "chunkCount": result["chunk_count"],
            "domain":     result["domain"],
            "status":     "COMPLETED",
        })

        logger.info(
            "Ingestion complete (stream): document_id=%s chunks=%d domain=%s",
            document_id, result["chunk_count"], result["domain"],
        )

    except Exception as e:
        logger.exception("Stream ingestion failed for document %s", document_id)
        await bus.publish(TOPIC_INGESTION_COMPLETE, {
            "documentId":   document_id,
            "tenantId":     tenant_id,
            "chunkCount":   0,
            "domain":       "general",
            "status":       "FAILED",
            "errorMessage": str(e),
        })
    finally:
        for p in tmp_paths:
            try:
                shutil.rmtree(p.parent, ignore_errors=True)
            except Exception:
                pass

    await bus.ack(TOPIC_DOCUMENTS_READY, _CONSUMER_GROUP, msg_id)
