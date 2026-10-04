"""
S3-compatible object storage — the one adapter every Python service uses.

Speaks plain S3 through boto3, so the server behind OBJECT_STORE_ENDPOINT is a
deployment choice (VersityGW locally, AWS S3 / R2 / Ceph RGW in production);
only the OBJECT_STORE_* environment changes.

Callers see two typed failures instead of botocore's string error codes:
  - ObjectNotFoundError   — the bucket or key does not exist; retrying cannot help.
  - ObjectStoreError — anything else (auth, network, server error) after the
                       client's own bounded retries.

Usage:
    store = ObjectStore.from_env()
    store.ensure_bucket(tenant_bucket("alpha"))
    store.put_bytes(tenant_bucket("alpha"), "docs/<hash>/original.txt", data, "text/plain")
    store.download_to(tenant_bucket("alpha"), "docs/<hash>/original.txt", Path("/tmp/x.txt"))
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

_DEFAULT_REGION = "us-east-1"

# S3 reports a missing bucket/key as NoSuchBucket/NoSuchKey on GET and as a
# bare "404"/"NotFound" on HEAD (no response body to carry a code).
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})

# Only "already owned by you" is benign on create: BucketAlreadyExists means
# another account owns the name, which is a real failure.
_ALREADY_OWNED_CODES = frozenset({"BucketAlreadyOwnedByYou"})

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def tenant_bucket(tenant_id: str) -> str:
    """Bucket holding one tenant's documents (document-service StorageService.bucketFor)."""
    return f"docintel-{tenant_id}"


class ObjectStoreConfigError(ValueError):
    """OBJECT_STORE_* settings are missing or malformed (raised at startup)."""


class ObjectStoreError(Exception):
    """An object-store call failed for a reason other than a missing bucket/key."""


class ObjectNotFoundError(ObjectStoreError):
    """The bucket or key does not exist. Terminal: a retry will see the same answer."""


@dataclass(frozen=True)
class ObjectStoreConfig:
    endpoint: str
    access_key: str
    secret_key: str = field(repr=False)
    region: str = _DEFAULT_REGION
    force_path_style: bool = True
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 60.0
    max_attempts: int = 3

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ObjectStoreConfig:
        """Build from OBJECT_STORE_* variables; the error names every missing setting."""
        env = os.environ if env is None else env
        required = ("OBJECT_STORE_ENDPOINT", "OBJECT_STORE_ACCESS_KEY", "OBJECT_STORE_SECRET_KEY")
        missing = [name for name in required if not env.get(name, "").strip()]
        if missing:
            raise ObjectStoreConfigError(
                f"Missing required object-store settings: {', '.join(missing)}"
            )

        endpoint = env["OBJECT_STORE_ENDPOINT"].strip()
        if not endpoint.startswith(("http://", "https://")):
            raise ObjectStoreConfigError(
                f"OBJECT_STORE_ENDPOINT must start with http:// or https:// (got {endpoint!r})"
            )

        return cls(
            endpoint=endpoint,
            access_key=env["OBJECT_STORE_ACCESS_KEY"].strip(),
            secret_key=env["OBJECT_STORE_SECRET_KEY"].strip(),
            region=env.get("OBJECT_STORE_REGION", "").strip() or _DEFAULT_REGION,
            force_path_style=_parse_bool(
                "OBJECT_STORE_FORCE_PATH_STYLE", env.get("OBJECT_STORE_FORCE_PATH_STYLE"), True
            ),
        )


def _parse_bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ObjectStoreConfigError(f"{name} must be true or false (got {raw!r})")


def _build_client(config: ObjectStoreConfig) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
        region_name=config.region,
        config=Config(
            signature_version="s3v4",
            connect_timeout=config.connect_timeout_s,
            read_timeout=config.read_timeout_s,
            # "standard" retries only throttling/transient errors, with
            # exponential backoff and jitter; 4xx client errors fail fast.
            retries={"mode": "standard", "max_attempts": config.max_attempts},
            s3={"addressing_style": "path" if config.force_path_style else "auto"},
        ),
    )


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _translate(exc: ClientError | BotoCoreError, operation: str, target: str) -> ObjectStoreError:
    if isinstance(exc, ClientError):
        code = _error_code(exc)
        if code in _NOT_FOUND_CODES:
            return ObjectNotFoundError(f"{operation} {target}: {code}")
        return ObjectStoreError(f"{operation} {target} failed: {code or exc}")
    return ObjectStoreError(f"{operation} {target} failed: {exc}")


class ObjectStore:
    """Thread-safe (boto3 clients are); build once per process and share."""

    def __init__(self, config: ObjectStoreConfig, client: Any | None = None) -> None:
        self._region = config.region
        self._client = client if client is not None else _build_client(config)

    @classmethod
    def from_env(cls) -> ObjectStore:
        return cls(ObjectStoreConfig.from_env())

    def ensure_bucket(self, bucket: str) -> None:
        """Create the bucket unless it exists. Safe under concurrent callers."""
        try:
            self._client.head_bucket(Bucket=bucket)
            return
        except ClientError as exc:
            if _error_code(exc) not in _NOT_FOUND_CODES:
                raise _translate(exc, "head bucket", bucket) from exc
        except BotoCoreError as exc:
            raise _translate(exc, "head bucket", bucket) from exc

        request: dict[str, Any] = {"Bucket": bucket}
        if self._region != _DEFAULT_REGION:
            request["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
        try:
            self._client.create_bucket(**request)
            logger.info("Created bucket %s", bucket)
        except ClientError as exc:
            if _error_code(exc) in _ALREADY_OWNED_CODES:
                return  # a concurrent caller created it between our HEAD and CREATE
            raise _translate(exc, "create bucket", bucket) from exc
        except BotoCoreError as exc:
            raise _translate(exc, "create bucket", bucket) from exc

    def put_bytes(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        except (ClientError, BotoCoreError) as exc:
            raise _translate(exc, "put", f"{bucket}/{key}") from exc

    def download_to(self, bucket: str, key: str, dest: Path) -> None:
        """Stream the object to dest. Raises ObjectNotFoundError if the bucket or key is missing."""
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            try:
                with dest.open("wb") as out:
                    shutil.copyfileobj(body, out)
            finally:
                body.close()
        except (ClientError, BotoCoreError) as exc:
            raise _translate(exc, "get", f"{bucket}/{key}") from exc
