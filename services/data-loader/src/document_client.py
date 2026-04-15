"""
Document service client.

Calls document-service /internal/documents/* to register files, manage data
sources, and persist chunks. Authenticates with HMAC service tokens so
document-service can validate the caller without a JWT.
"""

import logging
from uuid import UUID

import httpx

from docintel_common.internal_auth import compute_service_token
from docintel_common.tracing import TraceContext

from .config import get_settings

logger = logging.getLogger(__name__)


def _headers(tenant_id: str) -> dict[str, str]:
    cfg = get_settings()
    if not cfg.internal_gateway_secret:
        raise RuntimeError(
            "INTERNAL_GATEWAY_SECRET not configured — refusing unauthenticated "
            "internal call from data-loader (fail-secure)"
        )
    token = compute_service_token(tenant_id, cfg.internal_gateway_secret)
    headers: dict[str, str] = {
        "X-Tenant-Id": tenant_id,
        "Content-Type": "application/json",
        "X-Internal-Service-Token": token,
    }
    request_id = TraceContext.get_request_id()
    if request_id and request_id != "-":
        headers["X-Request-Id"] = request_id
    return headers


async def register_from_path(
    *,
    tenant_id: str,
    minio_path: str,
    content_hash: str,
    filename: str,
    file_size: int,
    content_type: str = "text/plain",
    data_source_id: UUID | None = None,
    metadata: dict | None = None,
    domain_hint: str = "auto",
) -> dict:
    cfg = get_settings()
    url = f"{cfg.document_service_url}/internal/documents/from-path"

    payload: dict = {
        "minioPath": minio_path,
        "contentHash": content_hash,
        "filename": filename,
        "fileSize": file_size,
        "contentType": content_type,
        "metadata": metadata or {},
        "domainHint": domain_hint,
    }
    if data_source_id is not None:
        payload["dataSourceId"] = str(data_source_id)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=_headers(tenant_id))
        resp.raise_for_status()
        return resp.json()


async def create_data_source(
    *,
    tenant_id: str,
    source_type: str,
    source_config: dict,
) -> dict:
    cfg = get_settings()
    url = f"{cfg.document_service_url}/internal/documents/data-sources"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            json={"sourceType": source_type, "sourceConfig": source_config},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()
        return resp.json()


async def complete_data_source(
    *,
    tenant_id: str,
    data_source_id: UUID,
    document_count: int,
) -> None:
    cfg = get_settings()
    url = f"{cfg.document_service_url}/internal/documents/data-sources/{data_source_id}/complete"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url,
            params={"document_count": document_count},
            headers=_headers(tenant_id),
        )
        resp.raise_for_status()


async def fail_data_source(*, tenant_id: str, data_source_id: UUID) -> None:
    cfg = get_settings()
    url = f"{cfg.document_service_url}/internal/documents/data-sources/{data_source_id}/fail"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, headers=_headers(tenant_id))
        resp.raise_for_status()
