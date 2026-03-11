"""MinIO source adapter — fetches objects via direct credentials (no presigned URLs)."""

import asyncio
import logging
import tempfile
from pathlib import Path

from minio import Minio

from ..config import get_settings
from .base import SourceAdapter

logger = logging.getLogger(__name__)


class MinIOAdapter(SourceAdapter):
    """
    Download a single object from MinIO using service-account credentials.

    source_ref keys:
        bucket      (str): MinIO bucket name (e.g. "docintel-tenant1")
        object_path (str): Object path within the bucket (e.g. "uuid/original.pdf")
        filename    (str): Human-readable filename for the temp file suffix
    """

    def __init__(self):
        settings = get_settings()
        # Strip http:// or https:// prefix since Minio client takes host:port
        endpoint = settings.minio_url.replace("http://", "").replace("https://", "")
        secure = settings.minio_url.startswith("https://")
        self._client = Minio(
            endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=secure,
        )

    def source_type(self) -> str:
        return "minio"

    async def fetch(self, source_ref: dict) -> list[Path]:
        bucket = source_ref["bucket"]
        object_path = source_ref["object_path"]
        filename = source_ref.get("filename", "document")

        suffix = Path(filename).suffix or ".bin"
        tmp_dir = Path(tempfile.mkdtemp(prefix="ingest_minio_"))
        local_path = tmp_dir / f"original{suffix}"

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._download,
            bucket,
            object_path,
            str(local_path),
        )

        logger.info("Downloaded %s/%s → %s", bucket, object_path, local_path)
        return [local_path]

    def _download(self, bucket: str, object_path: str, dest: str) -> None:
        self._client.fget_object(bucket, object_path, dest)
