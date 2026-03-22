"""
MinIO client for content-addressable file uploads.

Path convention: {tenant_id}/docs/{content_hash}/original.{ext}
  - Content-addressable: same content → same path → PUT is idempotent
  - Tenant-scoped: bucket = docintel-{tenant_id}
  - Extension preserved: ingestion-service can infer MIME type from extension
"""

import hashlib
import io
import logging
from pathlib import PurePosixPath

from minio import Minio
from minio.error import S3Error

from .config import get_settings

logger = logging.getLogger(__name__)

_client: Minio | None = None


def _get_client() -> Minio:
    global _client
    if _client is None:
        cfg = get_settings()
        _client = Minio(
            cfg.minio_url.removeprefix("http://").removeprefix("https://"),
            access_key=cfg.minio_access_key,
            secret_key=cfg.minio_secret_key,
            secure=cfg.minio_url.startswith("https://"),
        )
    return _client


def compute_content_hash(tenant_id: str, content: bytes) -> str:
    """SHA-256(tenant_id + content) — matches DocumentService.computeContentId."""
    digest = hashlib.sha256()
    digest.update(tenant_id.encode("utf-8"))
    digest.update(content)
    return digest.hexdigest()


def object_path(content_hash: str, filename: str) -> str:
    """Content-addressable MinIO object path (relative to bucket)."""
    ext = PurePosixPath(filename).suffix.lstrip(".") or "bin"
    return f"docs/{content_hash}/original.{ext}"


def upload_file(
    tenant_id: str,
    content_hash: str,
    content: bytes,
    filename: str,
    content_type: str = "text/plain",
) -> str:
    """
    Upload file bytes to MinIO at the content-addressable path.

    Returns the object path (relative to bucket). The upload is idempotent:
    if the same content_hash already exists the PUT simply overwrites with
    identical bytes.

    Raises:
        S3Error: on MinIO communication failure
    """
    client = _get_client()
    bucket = f"docintel-{tenant_id}"
    obj_path = object_path(content_hash, filename)

    try:
        client.bucket_exists(bucket)
    except S3Error:
        pass

    try:
        found = client.bucket_exists(bucket)
        if not found:
            client.make_bucket(bucket)
            logger.info("Created MinIO bucket: %s", bucket)
    except S3Error as e:
        logger.warning("Could not ensure MinIO bucket %s: %s", bucket, e)

    client.put_object(
        bucket_name=bucket,
        object_name=obj_path,
        data=io.BytesIO(content),
        length=len(content),
        content_type=content_type,
    )
    logger.debug("Uploaded %s/%s (%d bytes)", bucket, obj_path, len(content))
    return obj_path
