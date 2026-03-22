"""
Thin async Redis Streams publisher for the data-loader service.

data-loader only PRODUCES to files.available — it does not consume any streams.
Keeping the implementation local avoids pulling in the ML-heavy docintel-common
library (torch, transformers) into a lightweight data-loader container.
"""

import json
import logging

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

TOPIC_FILES_AVAILABLE = "files.available"
_DOCUMENT_SERVICE_GROUP = "document-service"


class StreamPublisher:
    """Async publisher for the files.available Redis Stream."""

    def __init__(self, host: str, port: int, password: str | None = None) -> None:
        self._redis = aioredis.Redis(
            host=host,
            port=port,
            password=password,
            db=0,
            decode_responses=True,
        )

    async def ensure_consumer_group(self) -> None:
        """
        Create the stream and the document-service consumer group if absent.
        Called once at startup; safe to call repeatedly (idempotent).
        """
        try:
            await self._redis.xgroup_create(
                name=TOPIC_FILES_AVAILABLE,
                groupname=_DOCUMENT_SERVICE_GROUP,
                id="$",
                mkstream=True,
            )
            logger.info(
                "Created consumer group '%s' on stream '%s'",
                _DOCUMENT_SERVICE_GROUP, TOPIC_FILES_AVAILABLE,
            )
        except Exception as e:
            if "BUSYGROUP" in str(e):
                pass  # already exists — idempotent
            else:
                logger.warning(
                    "Could not create consumer group on '%s': %s", TOPIC_FILES_AVAILABLE, e
                )

    async def publish_file_available(self, payload: dict) -> str:
        """
        Publish a files.available event.

        Payload keys mirror FilesAvailableEvent (camelCase, matching Jackson
        defaults in the Kotlin document-service consumer):
          minioPath, contentHash, tenantId, filename, contentType,
          fileSize, dataSourceId, domainHint, metadata
        """
        msg_id: str = await self._redis.xadd(
            TOPIC_FILES_AVAILABLE,
            {"payload": json.dumps(payload)},
        )
        logger.debug(
            "Published files.available: file=%s tenant=%s id=%s",
            payload.get("filename"), payload.get("tenantId"), msg_id,
        )
        return msg_id

    async def close(self) -> None:
        await self._redis.aclose()
