"""
Unit tests for docintel_common.object_store: config validation and the mapping
of S3 error codes to typed errors. Uses botocore's Stubber, which drives the
real boto3 client against canned responses, so request shapes are validated too.
"""

from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from docintel_common.object_store import (
    ObjectNotFoundError,
    ObjectStore,
    ObjectStoreConfig,
    ObjectStoreConfigError,
    ObjectStoreError,
    tenant_bucket,
)

VALID_ENV = {
    "OBJECT_STORE_ENDPOINT": "http://object-store:7070",
    "OBJECT_STORE_ACCESS_KEY": "access",
    "OBJECT_STORE_SECRET_KEY": "secret-value",
}


# ── config ────────────────────────────────────────────────────────────────────


def test_config_reads_required_settings_and_applies_defaults():
    config = ObjectStoreConfig.from_env(VALID_ENV)

    assert config.endpoint == "http://object-store:7070"
    assert config.access_key == "access"
    assert config.secret_key == "secret-value"
    assert config.region == "us-east-1"
    assert config.force_path_style is True


def test_config_names_every_missing_setting():
    with pytest.raises(ObjectStoreConfigError) as err:
        ObjectStoreConfig.from_env({"OBJECT_STORE_ENDPOINT": "http://x:1"})

    assert "OBJECT_STORE_ACCESS_KEY" in str(err.value)
    assert "OBJECT_STORE_SECRET_KEY" in str(err.value)


def test_config_treats_blank_values_as_missing():
    with pytest.raises(ObjectStoreConfigError, match="OBJECT_STORE_SECRET_KEY"):
        ObjectStoreConfig.from_env({**VALID_ENV, "OBJECT_STORE_SECRET_KEY": "   "})


def test_config_rejects_endpoint_without_scheme():
    with pytest.raises(ObjectStoreConfigError, match="http:// or https://"):
        ObjectStoreConfig.from_env({**VALID_ENV, "OBJECT_STORE_ENDPOINT": "object-store:7070"})


@pytest.mark.parametrize(
    "raw, expected", [("false", False), ("0", False), ("TRUE", True), ("yes", True)]
)
def test_config_parses_path_style_flag(raw, expected):
    config = ObjectStoreConfig.from_env({**VALID_ENV, "OBJECT_STORE_FORCE_PATH_STYLE": raw})
    assert config.force_path_style is expected


def test_config_rejects_unparseable_path_style_flag():
    with pytest.raises(ObjectStoreConfigError, match="OBJECT_STORE_FORCE_PATH_STYLE"):
        ObjectStoreConfig.from_env({**VALID_ENV, "OBJECT_STORE_FORCE_PATH_STYLE": "maybe"})


def test_config_uses_explicit_region():
    config = ObjectStoreConfig.from_env({**VALID_ENV, "OBJECT_STORE_REGION": "eu-central-1"})
    assert config.region == "eu-central-1"


def test_config_repr_never_contains_the_secret():
    assert "secret-value" not in repr(ObjectStoreConfig.from_env(VALID_ENV))


def test_tenant_bucket_prefixes_tenant_id():
    assert tenant_bucket("alpha") == "docintel-alpha"


# ── error mapping ─────────────────────────────────────────────────────────────


def _stubbed_store(region: str = "us-east-1") -> tuple[ObjectStore, Stubber]:
    client = boto3.client(
        "s3",
        endpoint_url="http://stub:7070",
        aws_access_key_id="a",
        aws_secret_access_key="b",
        region_name=region,
    )
    stubber = Stubber(client)
    config = ObjectStoreConfig(
        endpoint="http://stub:7070", access_key="a", secret_key="b", region=region
    )
    return ObjectStore(config, client=client), stubber


def test_ensure_bucket_skips_create_when_bucket_exists():
    store, stubber = _stubbed_store()
    stubber.add_response("head_bucket", {}, {"Bucket": "b1"})

    with stubber:
        store.ensure_bucket("b1")
    stubber.assert_no_pending_responses()


def test_ensure_bucket_creates_missing_bucket():
    store, stubber = _stubbed_store()
    stubber.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stubber.add_response("create_bucket", {}, {"Bucket": "b1"})

    with stubber:
        store.ensure_bucket("b1")
    stubber.assert_no_pending_responses()


def test_ensure_bucket_sends_location_constraint_outside_us_east_1():
    store, stubber = _stubbed_store(region="eu-central-1")
    stubber.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stubber.add_response(
        "create_bucket",
        {},
        {"Bucket": "b1", "CreateBucketConfiguration": {"LocationConstraint": "eu-central-1"}},
    )

    with stubber:
        store.ensure_bucket("b1")
    stubber.assert_no_pending_responses()


def test_ensure_bucket_accepts_losing_a_create_race():
    store, stubber = _stubbed_store()
    stubber.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stubber.add_client_error(
        "create_bucket", service_error_code="BucketAlreadyOwnedByYou", http_status_code=409
    )

    with stubber:
        store.ensure_bucket("b1")


def test_ensure_bucket_fails_when_another_account_owns_the_name():
    store, stubber = _stubbed_store()
    stubber.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
    stubber.add_client_error(
        "create_bucket", service_error_code="BucketAlreadyExists", http_status_code=409
    )

    with stubber, pytest.raises(ObjectStoreError) as err:
        store.ensure_bucket("b1")
    assert not isinstance(err.value, ObjectNotFoundError)


def test_ensure_bucket_surfaces_access_denied_without_creating():
    store, stubber = _stubbed_store()
    stubber.add_client_error("head_bucket", service_error_code="403", http_status_code=403)

    with stubber, pytest.raises(ObjectStoreError, match="403"):
        store.ensure_bucket("b1")
    stubber.assert_no_pending_responses()


@pytest.mark.parametrize("code", ["NoSuchKey", "NoSuchBucket"])
def test_download_maps_missing_object_or_bucket_to_object_not_found(code, tmp_path: Path):
    store, stubber = _stubbed_store()
    stubber.add_client_error("get_object", service_error_code=code, http_status_code=404)

    with stubber, pytest.raises(ObjectNotFoundError, match=code):
        store.download_to("b1", "docs/x/original.txt", tmp_path / "out")


def test_download_maps_other_failures_to_object_store_error(tmp_path: Path):
    store, stubber = _stubbed_store()
    stubber.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403)

    with stubber, pytest.raises(ObjectStoreError) as err:
        store.download_to("b1", "docs/x/original.txt", tmp_path / "out")
    assert not isinstance(err.value, ObjectNotFoundError)


def test_put_maps_failures_to_object_store_error():
    store, stubber = _stubbed_store()
    stubber.add_client_error("put_object", service_error_code="InternalError", http_status_code=500)

    with stubber, pytest.raises(ObjectStoreError, match="InternalError"):
        store.put_bytes("b1", "k", b"data", "text/plain")
