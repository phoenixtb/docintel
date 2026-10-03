"""Object-store source adapter — downloads one document blob to a local temp file."""

import asyncio
import logging
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from docintel_common.object_store import ObjectStore

from .base import SourceAdapter

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def default_object_store() -> ObjectStore:
    """Process-wide store built from OBJECT_STORE_*; called at startup to fail fast."""
    return ObjectStore.from_env()


class ObjectStoreAdapter(SourceAdapter):
    """
    Download a single object using the service's object-store credentials.

    source_ref keys:
        bucket      (str): tenant bucket (e.g. "docintel-alpha")
        object_path (str): key within the bucket (e.g. "docs/<hash>/original.pdf")
        filename    (str): human-readable filename; its suffix names the temp file

    Raises docintel_common.object_store.ObjectNotFoundError when the bucket or
    key is missing (terminal for the caller) and ObjectStoreError otherwise.
    """

    def __init__(self, store: ObjectStore) -> None:
        self._store = store

    def source_type(self) -> str:
        return "s3"

    async def fetch(self, source_ref: dict) -> list[Path]:
        bucket = source_ref["bucket"]
        object_path = source_ref["object_path"]
        filename = source_ref.get("filename", "document")

        suffix = Path(filename).suffix or ".bin"
        tmp_dir = Path(tempfile.mkdtemp(prefix="ingest_obj_"))
        local_path = tmp_dir / f"original{suffix}"

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, self._store.download_to, bucket, object_path, local_path
            )
        except BaseException:
            # The caller only cleans up paths we return; on failure there are none.
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        logger.info("Downloaded %s/%s → %s", bucket, object_path, local_path)
        return [local_path]
