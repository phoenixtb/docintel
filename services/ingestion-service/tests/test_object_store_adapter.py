"""ObjectStoreAdapter: materialises one object as a temp file, cleans up on failure."""

from pathlib import Path

import pytest
from docintel_common.object_store import ObjectNotFoundError

from src.adapters import ObjectStoreAdapter


class FakeStore:
    """Stands in for docintel_common.object_store.ObjectStore."""

    def __init__(self, content: bytes | None) -> None:
        self._content = content
        self.dest: Path | None = None

    def download_to(self, bucket: str, key: str, dest: Path) -> None:
        self.dest = dest
        if self._content is None:
            raise ObjectNotFoundError(f"get {bucket}/{key}: NoSuchKey")
        dest.write_bytes(self._content)


async def test_fetch_writes_object_to_temp_file_named_by_source_suffix():
    adapter = ObjectStoreAdapter(FakeStore(b"%PDF"))

    (path,) = await adapter.fetch(
        {"bucket": "docintel-a", "object_path": "docs/h/original.pdf", "filename": "Q3 report.pdf"}
    )

    try:
        assert path.name == "original.pdf"
        assert path.read_bytes() == b"%PDF"
    finally:
        path.unlink()
        path.parent.rmdir()


async def test_fetch_removes_temp_dir_when_object_is_missing():
    store = FakeStore(None)
    adapter = ObjectStoreAdapter(store)

    with pytest.raises(ObjectNotFoundError):
        await adapter.fetch(
            {"bucket": "docintel-a", "object_path": "docs/h/x.txt", "filename": "x.txt"}
        )

    assert store.dest is not None
    assert not store.dest.parent.exists()


def test_source_type_is_s3():
    assert ObjectStoreAdapter(FakeStore(b"")).source_type() == "s3"
