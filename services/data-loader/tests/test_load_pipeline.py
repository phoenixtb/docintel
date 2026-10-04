"""
Producer side of the files.available contract: what _load_dataset_background
uploads and publishes for each loaded file.
"""

from unittest.mock import patch
from uuid import uuid4

from src.adapters import LoadedFile
from src.api.main import _load_dataset_background
from src.job_registry import JobRegistry
from tests.test_storage import RecordingStore

# Required keys of FilesAvailableEvent (document-service StreamTopics.kt).
FILES_AVAILABLE_REQUIRED = {"objectPath", "contentHash", "tenantId", "filename"}


class RecordingPublisher:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def publish_file_available(self, payload: dict) -> str:
        self.payloads.append(payload)
        return "1-1"


class OneFileAdapter:
    def fetch(self, config: dict, tenant_id: str):
        yield LoadedFile(content=b"leave policy", filename="hr-1.txt", metadata={"row": "1"})


async def test_publishes_object_path_of_the_uploaded_file():
    store, publisher, registry = RecordingStore(), RecordingPublisher(), JobRegistry()
    job_id = registry.create("alpha", ["hr_policies"])

    with patch("src.api.main.HuggingFaceAdapter", OneFileAdapter):
        published = await _load_dataset_background(
            dataset_key="hr_policies",
            tenant_id="alpha",
            samples=1,
            data_source_id=uuid4(),
            registry=registry,
            job_id=job_id,
            publisher=publisher,
            store=store,
        )

    assert published == 1
    (payload,) = publisher.payloads
    assert FILES_AVAILABLE_REQUIRED <= payload.keys()
    assert "minioPath" not in payload
    uploaded_key = store.calls[-1][2]
    assert payload["objectPath"] == uploaded_key
    assert payload["tenantId"] == "alpha"


async def test_skips_publish_when_upload_fails():
    class FailingStore(RecordingStore):
        def put_bytes(self, bucket, key, data, content_type):
            raise RuntimeError("object store down")

    publisher, registry = RecordingPublisher(), JobRegistry()
    job_id = registry.create("alpha", ["hr_policies"])

    with patch("src.api.main.HuggingFaceAdapter", OneFileAdapter):
        published = await _load_dataset_background(
            dataset_key="hr_policies",
            tenant_id="alpha",
            samples=1,
            data_source_id=uuid4(),
            registry=registry,
            job_id=job_id,
            publisher=publisher,
            store=FailingStore(),
        )

    assert published == 0
    assert publisher.payloads == []
