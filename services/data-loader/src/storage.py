"""
Content-addressable document upload for the data loader.

Key convention (shared with document-service StorageService):
  bucket = docintel-{tenant_id}, key = docs/{content_hash}/original.{ext}
  - Content-addressable: same content → same key → PUT is idempotent
  - Tenant-scoped: one bucket per tenant
  - Extension preserved: ingestion-service infers the MIME type from it
"""

import hashlib
import logging
from pathlib import PurePosixPath

from docintel_common.object_store import ObjectStore, tenant_bucket

logger = logging.getLogger(__name__)


def compute_content_hash(tenant_id: str, content: bytes) -> str:
    """SHA-256(tenant_id + content) — matches DocumentService.computeContentId."""
    digest = hashlib.sha256()
    digest.update(tenant_id.encode("utf-8"))
    digest.update(content)
    return digest.hexdigest()


def object_path(content_hash: str, filename: str) -> str:
    """Content-addressable object key (relative to the tenant bucket)."""
    ext = PurePosixPath(filename).suffix.lstrip(".") or "bin"
    return f"docs/{content_hash}/original.{ext}"


def upload_file(
    store: ObjectStore,
    *,
    tenant_id: str,
    content_hash: str,
    content: bytes,
    filename: str,
    content_type: str = "text/plain",
) -> str:
    """
    Upload file bytes to the tenant bucket at the content-addressable key.

    Returns the object key. Idempotent: re-uploading the same content writes the
    same bytes to the same key.

    Raises:
        ObjectStoreError: the bucket could not be ensured or the PUT failed.
    """
    bucket = tenant_bucket(tenant_id)
    key = object_path(content_hash, filename)
    store.ensure_bucket(bucket)
    store.put_bytes(bucket, key, content, content_type)
    logger.debug("Uploaded %s/%s (%d bytes)", bucket, key, len(content))
    return key
