"""
Ingestion Service — FastAPI application.

Endpoints:
  POST /ingest                — trigger async ingestion of a document from MinIO
  DELETE /vectors/{tid}/{did} — delete document vectors from Qdrant
  DELETE /vectors/{tid}       — delete all tenant vectors from Qdrant
  GET /health
  GET /metrics

Auth: all non-health endpoints require the gateway-injected X-Internal-Service-Token.
tenant_id is always taken from the gateway-injected X-Tenant-Id header (never
trusted from the request body) to prevent cross-tenant isolation bypass.

Dataset loading (previously /ingest/dataset/*) has been moved to the data-loader
service which uses content-addressed dedup and proper data source lifecycle tracking.
"""

import asyncio
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..job_registry import JobRegistry

from docintel_common.internal_auth import verify_internal_token
from docintel_common.security import CLASSIFICATION_ORDER, Classification, DocumentACL
from docintel_common.tracing import TraceContext, configure_trace_logging

from ..adapters import MinIOAdapter
from ..config import get_settings
from ..stream_worker import run_stream_worker
from ..document_client import ChunkPayload, DocumentServiceClient
from ..pipeline import invalidate_pipeline_cache, run_ingestion
from ..stores import delete_document_from_store, delete_tenant_from_store, invalidate_cache_for_tenant

logger = logging.getLogger(__name__)

_METRICS_ENABLED = False
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    from prometheus_client import Counter

    _METRICS_ENABLED = True
except ImportError:
    pass


_job_registry = JobRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Ingestion service starting up")
    app.state.job_registry = _job_registry

    async def _eviction_loop() -> None:
        while True:
            await asyncio.sleep(60)
            _job_registry.evict_expired()

    settings = get_settings()
    eviction_task = asyncio.create_task(_eviction_loop())
    stream_task: asyncio.Task | None = None
    if settings.stream_consumer_enabled:
        stream_task = asyncio.create_task(run_stream_worker(settings))
        logger.info("Redis Stream consumer task started")

    try:
        yield
    finally:
        eviction_task.cancel()
        if stream_task is not None:
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
        logger.info("Ingestion service shutting down")


app = FastAPI(
    title="DocIntel Ingestion Service",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

if _METRICS_ENABLED:
    Instrumentator().instrument(app).expose(app)

configure_trace_logging()


@app.middleware("http")
async def tracing_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    tenant_id  = request.headers.get("X-Tenant-Id", "-")
    user_id    = request.headers.get("X-User-Id", "-")
    TraceContext.set(request_id, tenant_id, user_id)
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


# =============================================================================
# Gateway auth dependency
# =============================================================================


def require_internal_token(
    request: Request,
    x_internal_service_token: Annotated[str | None, Header(alias="X-Internal-Service-Token")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-Id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> None:
    """
    Verify X-Internal-Service-Token HMAC before trusting any X-* headers.
    Rejects requests that did not originate from the API Gateway.
    """
    import os
    secret = os.environ.get("INTERNAL_GATEWAY_SECRET", "")
    if not secret:
        logger.error("INTERNAL_GATEWAY_SECRET is not set; rejecting all requests.")
        raise HTTPException(status_code=403, detail="Service misconfiguration.")

    token = x_internal_service_token or ""
    if not verify_internal_token(
        token=token,
        request_id=x_request_id or "",
        tenant_id=x_tenant_id or "",
        user_id=x_user_id or "",
        secret=secret,
    ):
        logger.warning(
            "Invalid X-Internal-Service-Token — request may have bypassed the gateway "
            "(request_id=%s, tenant=%s, user=%s)",
            x_request_id, x_tenant_id, x_user_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Missing or invalid internal service token. All requests must pass through the API Gateway.",
        )


def get_tenant_id(
    _: Annotated[None, Depends(require_internal_token)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> str:
    return x_tenant_id or "default"


def get_user_clearance(
    _: Annotated[None, Depends(require_internal_token)],
    x_user_clearance: Annotated[str | None, Header(alias="X-User-Clearance")] = None,
) -> Classification:
    try:
        return Classification((x_user_clearance or "internal").lower())
    except ValueError:
        return Classification.INTERNAL


GatewayUserDep = Annotated[None, Depends(require_internal_token)]
TenantDep = Annotated[str, Depends(get_tenant_id)]
UserClearanceDep = Annotated[Classification, Depends(get_user_clearance)]


# =============================================================================
# Request / Response models
# =============================================================================


class IngestRequest(BaseModel):
    document_id: str
    tenant_id: str = Field(default="default")
    bucket: str
    object_path: str
    filename: str = Field(default="document")
    domain_hint: str = Field(default="auto")
    metadata: dict = Field(default_factory=dict)
    acl: DocumentACL = Field(default_factory=DocumentACL)


class IngestResponse(BaseModel):
    status: str
    document_id: str


class VectorDeleteResponse(BaseModel):
    deleted: bool
    document_id: Optional[str] = None
    tenant_id: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    service: str = "ingestion-service"


# =============================================================================
# Background ingestion task
# =============================================================================


async def _ingest_document_background(
    request: IngestRequest,
    effective_tenant_id: str,
    acl: DocumentACL | None = None,
) -> None:
    """
    Full ingestion pipeline executed in a background task:
      1. Download from MinIO
      2. DoclingConverter → BM25 + Ollama embed → Qdrant write
      3. Persist chunks to PG
      4. Update document status in PG
    """
    adapter = MinIOAdapter()
    tmp_paths: list[Path] = []

    try:
        tmp_paths = await adapter.fetch(
            {
                "bucket": request.bucket,
                "object_path": request.object_path,
                "filename": request.filename,
            }
        )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            run_ingestion,
            tmp_paths,
            request.document_id,
            effective_tenant_id,
            request.filename,
            request.domain_hint,
            request.metadata,
            None,
            acl or request.acl,
        )

        chunk_payloads = [
            ChunkPayload(
                chunk_id=c["chunk_id"],
                chunk_index=c["chunk_index"],
                content=c["content"],
                start_char=c["start_char"],
                end_char=c["end_char"],
                token_count=c["token_count"],
                metadata=c["metadata"],
            )
            for c in result["chunks"]
        ]

        doc_client = DocumentServiceClient()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            doc_client.persist_chunks,
            request.document_id,
            effective_tenant_id,
            chunk_payloads,
        )

        logger.info(
            "Ingestion complete: document_id=%s chunks=%d domain=%s",
            request.document_id,
            result["chunk_count"],
            result["domain"],
        )

    except Exception as e:
        logger.exception("Ingestion failed for document %s", request.document_id)
        # Status update on failure is handled by document-service via the Redis
        # ingestion.complete stream consumer (IngestionCompleteConsumer).
        # We still log here but no longer write directly to the DB.
    finally:
        for p in tmp_paths:
            try:
                shutil.rmtree(p.parent, ignore_errors=True)
            except Exception:
                pass


# =============================================================================
# Endpoints
# =============================================================================


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.post("/ingest", response_model=IngestResponse, status_code=202)
async def ingest_document(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    _user_id: GatewayUserDep,
    tenant_id: TenantDep,
    user_clearance: UserClearanceDep,
):
    """
    Accept a document ingestion job and process it asynchronously.

    document-service uploads the file to MinIO and then calls this endpoint
    with the bucket + object_path. Returns 202 Accepted immediately; the
    ingestion-service updates document status in PG on completion.
    """
    if CLASSIFICATION_ORDER[request.acl.classification] > CLASSIFICATION_ORDER[user_clearance]:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Cannot classify document as '{request.acl.classification.value}': "
                f"your clearance is '{user_clearance.value}'."
            ),
        )

    logger.info(
        "Ingestion job accepted: document_id=%s tenant=%s file=%s classification=%s",
        request.document_id,
        tenant_id,
        request.filename,
        request.acl.classification.value,
    )
    background_tasks.add_task(_ingest_document_background, request, tenant_id, request.acl)
    return IngestResponse(status="accepted", document_id=request.document_id)


@app.delete("/vectors/{tenant_id}/{document_id}", response_model=VectorDeleteResponse)
async def delete_document_vectors(
    tenant_id: str,
    document_id: str,
    _user_id: GatewayUserDep,
    header_tenant_id: TenantDep,
):
    """Delete all Qdrant vectors for a specific document."""
    if header_tenant_id != "default" and tenant_id != header_tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete vectors for tenant '{tenant_id}': caller is tenant '{header_tenant_id}'.",
        )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, delete_document_from_store, tenant_id, document_id)
    await loop.run_in_executor(None, invalidate_cache_for_tenant, tenant_id)
    return VectorDeleteResponse(deleted=True, document_id=document_id)


@app.delete("/vectors/{tenant_id}", response_model=VectorDeleteResponse)
async def delete_tenant_vectors(
    tenant_id: str,
    _user_id: GatewayUserDep,
    header_tenant_id: TenantDep,
):
    """Delete all Qdrant vectors and PG chunks for an entire tenant."""
    if header_tenant_id != "default" and tenant_id != header_tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete vectors for tenant '{tenant_id}': caller is tenant '{header_tenant_id}'.",
        )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, delete_tenant_from_store, tenant_id)
    await loop.run_in_executor(None, invalidate_cache_for_tenant, tenant_id)
    invalidate_pipeline_cache(tenant_id)
    return VectorDeleteResponse(deleted=True, tenant_id=tenant_id)
