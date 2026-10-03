"""upload_file: tenant bucket + content-addressable key, ensured before the PUT."""

from src.storage import compute_content_hash, object_path, upload_file


class RecordingStore:
    """Stands in for docintel_common.object_store.ObjectStore; records calls in order."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def ensure_bucket(self, bucket: str) -> None:
        self.calls.append(("ensure_bucket", bucket))

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        self.calls.append(("put_bytes", bucket, key, data, content_type))


def test_upload_ensures_tenant_bucket_then_writes_content_addressable_key():
    store = RecordingStore()

    key = upload_file(
        store,
        tenant_id="alpha",
        content_hash="h" * 64,
        content=b"hello",
        filename="notes.md",
        content_type="text/markdown",
    )

    assert key == f"docs/{'h' * 64}/original.md"
    assert store.calls == [
        ("ensure_bucket", "docintel-alpha"),
        ("put_bytes", "docintel-alpha", key, b"hello", "text/markdown"),
    ]


def test_object_path_defaults_extension_to_bin():
    assert object_path("abc", "README") == "docs/abc/original.bin"


def test_content_hash_is_tenant_scoped():
    assert compute_content_hash("alpha", b"same") != compute_content_hash("beta", b"same")
