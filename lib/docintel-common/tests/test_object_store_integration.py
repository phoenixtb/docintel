"""
docintel_common.object_store against a real S3 server (the VersityGW image the
compose stack runs). Requires Docker; Testcontainers starts and removes the
container, so no infrastructure has to be running beforehand.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import HttpWaitStrategy

from docintel_common.object_store import (
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreConfig,
    ObjectStoreError,
)

# Keep in sync with the object-store service in docker-compose.yml
# (enforced by scripts/check-object-store-pin.sh).
VERSITYGW_IMAGE = "versity/versitygw:v1.8.0"
ACCESS_KEY = "test-access"
SECRET_KEY = "test-secret-key"


@pytest.fixture(scope="module")
def endpoint() -> Iterator[str]:
    container = (
        DockerContainer(VERSITYGW_IMAGE)
        .with_env("ROOT_ACCESS_KEY", ACCESS_KEY)
        .with_env("ROOT_SECRET_KEY", SECRET_KEY)
        .with_command("--port :7070 --health /health --quiet posix /tmp")
        .with_exposed_ports(7070)
        .waiting_for(HttpWaitStrategy(7070, "/health").for_status_code(200))
    )
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(7070)}"


@pytest.fixture
def store(endpoint: str) -> ObjectStore:
    return ObjectStore(
        ObjectStoreConfig(endpoint=endpoint, access_key=ACCESS_KEY, secret_key=SECRET_KEY)
    )


@pytest.fixture
def bucket() -> str:
    return f"docintel-it-{uuid.uuid4().hex[:12]}"


def test_round_trips_bytes_through_a_new_bucket(store: ObjectStore, bucket: str, tmp_path: Path):
    payload = b"%PDF-1.7 \x00\xff binary payload"

    store.ensure_bucket(bucket)
    store.put_bytes(bucket, "docs/abc/original.pdf", payload, "application/pdf")
    store.download_to(bucket, "docs/abc/original.pdf", tmp_path / "out.pdf")

    assert (tmp_path / "out.pdf").read_bytes() == payload


def test_ensure_bucket_is_idempotent(store: ObjectStore, bucket: str):
    store.ensure_bucket(bucket)
    store.ensure_bucket(bucket)


def test_overwriting_the_same_key_keeps_the_latest_bytes(
    store: ObjectStore, bucket: str, tmp_path: Path
):
    store.ensure_bucket(bucket)
    store.put_bytes(bucket, "k", b"first", "text/plain")
    store.put_bytes(bucket, "k", b"second", "text/plain")

    store.download_to(bucket, "k", tmp_path / "out")

    assert (tmp_path / "out").read_bytes() == b"second"


def test_missing_key_raises_object_not_found(store: ObjectStore, bucket: str, tmp_path: Path):
    store.ensure_bucket(bucket)

    with pytest.raises(ObjectNotFoundError):
        store.download_to(bucket, "docs/missing/original.txt", tmp_path / "out")


def test_missing_bucket_raises_object_not_found(store: ObjectStore, bucket: str, tmp_path: Path):
    with pytest.raises(ObjectNotFoundError):
        store.download_to(bucket, "docs/any/original.txt", tmp_path / "out")


def test_wrong_credentials_are_an_error_not_a_missing_object(
    endpoint: str, bucket: str, tmp_path: Path
):
    intruder = ObjectStore(
        ObjectStoreConfig(
            endpoint=endpoint, access_key=ACCESS_KEY, secret_key="wrong", max_attempts=1
        )
    )

    with pytest.raises(ObjectStoreError) as err:
        intruder.download_to(bucket, "k", tmp_path / "out")
    assert not isinstance(err.value, ObjectNotFoundError)
