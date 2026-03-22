"""
Data Loader Service — FastAPI application.

Endpoints:
  GET  /datasets                           — list available sample datasets
  POST /datasets/load                      — start async bulk dataset load job
  GET  /datasets/load/{job_id}/progress    — SSE stream for job progress
  GET  /health

All /datasets endpoints require the gateway-injected X-User-Id header (same
pattern as other internal services). tenant_id is always taken from X-Tenant-Id.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Optional
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..adapters import HuggingFaceAdapter, LoadedFile
from ..config import get_settings
from ..document_client import (
    complete_data_source,
    create_data_source,
    fail_data_source,
)
from ..job_registry import JobRegistry
from ..minio_client import compute_content_hash, upload_file
from ..stream_publisher import StreamPublisher

logger = logging.getLogger(__name__)

_METRICS_ENABLED = False
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    _METRICS_ENABLED = True
except ImportError:
    pass

_job_registry = JobRegistry()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Data loader service starting up")
    app.state.job_registry = _job_registry

    settings = get_settings()
    publisher = StreamPublisher(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
    )
    # Ensure the files.available stream + consumer group exist so document-service
    # consumers can subscribe before the first publish.
    await publisher.ensure_consumer_group()
    app.state.stream_publisher = publisher

    async def _eviction_loop() -> None:
        while True:
            await asyncio.sleep(60)
            _job_registry.evict_expired()

    eviction_task = asyncio.create_task(_eviction_loop())
    try:
        yield
    finally:
        eviction_task.cancel()
        await publisher.close()
        logger.info("Data loader service shutting down")


app = FastAPI(
    title="DocIntel Data Loader",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

if _METRICS_ENABLED:
    Instrumentator().instrument(app).expose(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


# =============================================================================
# Auth dependencies
# =============================================================================


def require_user_id(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> None:
    """Reject requests that did not originate from the API Gateway (no X-User-Id)."""
    if not x_user_id:
        raise HTTPException(
            status_code=403,
            detail="Missing X-User-Id header. All requests must pass through the API Gateway.",
        )


def get_tenant_id(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> str:
    return x_tenant_id or "default"


GatewayUserDep = Annotated[None, Header(alias="X-User-Id")]
TenantDep = Annotated[str, Header(alias="X-Tenant-Id", default="default")]


# =============================================================================
# Models
# =============================================================================


class DatasetInfo(BaseModel):
    key: str
    name: str
    domain: str
    description: str


class DatasetLoadRequest(BaseModel):
    datasets: list[str] = Field(default_factory=list)
    samples_per_dataset: int = Field(default=100, ge=1, le=10_000)
    tenant_id: str = Field(default="default")


class DatasetLoadResponse(BaseModel):
    status: str
    datasets: list[str]
    message: str  # job_id — kept in `message` for web-UI backward compat


class HealthResponse(BaseModel):
    status: str
    service: str = "data-loader"


# =============================================================================
# Available datasets
# =============================================================================

_AVAILABLE_DATASETS: list[DatasetInfo] = [
    DatasetInfo(
        key="techqa",
        name="TechQA",
        domain="technical",
        description="Technical documentation Q&A pairs",
    ),
    DatasetInfo(
        key="hr_policies",
        name="HR Policies",
        domain="hr_policy",
        description="HR policy Q&A pairs",
    ),
    DatasetInfo(
        key="cuad",
        name="Legal Cases",
        domain="contracts",
        description="European Court legal cases (CUAD)",
    ),
]

_DOMAIN_HINTS: dict[str, str] = {d.key: d.domain for d in _AVAILABLE_DATASETS}


# =============================================================================
# Background load task
# =============================================================================


async def _load_dataset_background(
    dataset_key: str,
    tenant_id: str,
    samples: int,
    data_source_id: UUID,
    registry: JobRegistry,
    job_id: str,
    publisher: "StreamPublisher",
) -> int:
    """
    Per-dataset load pipeline (async bus path):
      1. HuggingFaceAdapter.fetch() → LoadedFile iterator (sync, run in executor)
      2. For each file: compute SHA-256 → upload to MinIO → publish to files.available stream
      3. Emit SSE progress events after each published file
      4. Return published_count
    """
    adapter = HuggingFaceAdapter()
    domain_hint = _DOMAIN_HINTS.get(dataset_key, "auto")
    loop = asyncio.get_running_loop()

    files: list[LoadedFile] = await loop.run_in_executor(
        None,
        lambda: list(adapter.fetch({"dataset_key": dataset_key, "samples": samples}, tenant_id)),
    )

    if not files:
        return 0

    registry.set_total(job_id, len(files))
    published = 0

    for loaded_file in files:
        content_hash = compute_content_hash(tenant_id, loaded_file.content)

        try:
            minio_path = await loop.run_in_executor(
                None,
                lambda lf=loaded_file, ch=content_hash: upload_file(
                    tenant_id=tenant_id,
                    content_hash=ch,
                    content=lf.content,
                    filename=lf.filename,
                    content_type="text/plain",
                ),
            )
        except Exception as e:
            logger.warning(
                "MinIO upload failed for %s (dataset=%s): %s — skipping",
                loaded_file.filename, dataset_key, e,
            )
            continue

        try:
            await publisher.publish_file_available({
                "minioPath":    minio_path,
                "contentHash":  content_hash,
                "tenantId":     tenant_id,
                "filename":     loaded_file.filename,
                "contentType":  "text/plain",
                "fileSize":     len(loaded_file.content),
                "dataSourceId": str(data_source_id),
                "domainHint":   domain_hint,
                "metadata":     {
                    **loaded_file.metadata,
                    "source_dataset": dataset_key,
                },
            })
            published += 1
        except Exception as e:
            logger.warning(
                "Stream publish failed for %s (dataset=%s): %s — skipping",
                loaded_file.filename, dataset_key, e,
            )
            continue

        registry.file_done(
            job_id, loaded_file.filename, domain_hint, deduplicated=False
        )

    return published


async def _run_bulk_load(
    datasets: list[str],
    tenant_id: str,
    samples_per_dataset: int,
    registry: JobRegistry,
    job_id: str,
    publisher: "StreamPublisher",
) -> None:
    """
    Background worker: sequentially load each dataset.

    Files are uploaded to MinIO and published to the files.available stream.
    document-service consumes the stream asynchronously, so there is no
    synchronous dedup result here. The data source document_count reflects
    the number of files published (not final registered count after dedup).
    """
    total_published = 0

    for dataset_key in datasets:
        data_source: dict = {}
        try:
            data_source = await create_data_source(
                tenant_id=tenant_id,
                source_type="huggingface",
                source_config={
                    "dataset_key": dataset_key,
                    "samples": samples_per_dataset,
                },
            )
            data_source_id = UUID(data_source["id"])

            published = await _load_dataset_background(
                dataset_key=dataset_key,
                tenant_id=tenant_id,
                samples=samples_per_dataset,
                data_source_id=data_source_id,
                registry=registry,
                job_id=job_id,
                publisher=publisher,
            )
            total_published += published

            await complete_data_source(
                tenant_id=tenant_id,
                data_source_id=data_source_id,
                document_count=published,
            )
            logger.info(
                "Dataset load complete: key=%s tenant=%s published=%d",
                dataset_key, tenant_id, published,
            )

        except Exception:
            logger.exception("Dataset load failed for %s (tenant=%s)", dataset_key, tenant_id)
            if data_source.get("id"):
                try:
                    await fail_data_source(
                        tenant_id=tenant_id,
                        data_source_id=UUID(data_source["id"]),
                    )
                except Exception:
                    pass
            registry.fail(job_id, f"Failed to load dataset '{dataset_key}'")
            return

    registry.finish(job_id, registered=total_published, deduplicated=0)


# =============================================================================
# Endpoints
# =============================================================================


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/datasets")
async def list_datasets(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> dict:
    """Return the list of available sample datasets. Consumed by the web-UI Documents page."""
    if not x_user_id:
        raise HTTPException(status_code=403, detail="Missing X-User-Id header.")
    return {"available_datasets": [d.model_dump() for d in _AVAILABLE_DATASETS]}


@app.post("/datasets/load", response_model=DatasetLoadResponse, status_code=202)
async def start_dataset_load(
    request: DatasetLoadRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> DatasetLoadResponse:
    """
    Start bulk-loading sample datasets; returns 202 + job_id immediately.

    Subscribe to GET /datasets/load/{job_id}/progress for SSE progress updates.
    Isolation: job is scoped to the requesting tenant (JWT-derived X-Tenant-Id).
    """
    if not x_user_id:
        raise HTTPException(status_code=403, detail="Missing X-User-Id header.")

    if not request.datasets:
        raise HTTPException(status_code=400, detail="No datasets specified.")

    unknown = [k for k in request.datasets if k not in _DOMAIN_HINTS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown dataset key(s): {unknown}. Valid: {list(_DOMAIN_HINTS)}",
        )

    tenant_id = x_tenant_id or request.tenant_id or "default"
    registry: JobRegistry = http_request.app.state.job_registry
    job_id = registry.create(tenant_id, request.datasets)

    publisher: StreamPublisher = http_request.app.state.stream_publisher
    background_tasks.add_task(
        _run_bulk_load,
        request.datasets,
        tenant_id,
        request.samples_per_dataset,
        registry,
        job_id,
        publisher,
    )

    return DatasetLoadResponse(
        status="started",
        datasets=request.datasets,
        message=job_id,
    )


@app.get("/datasets/load/{job_id}/progress")
async def dataset_load_progress(
    job_id: str,
    http_request: Request,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> StreamingResponse:
    """
    SSE stream for dataset load job progress.

    Isolation: job_id is UUID4 (unguessable) AND we validate X-Tenant-Id so
    cross-tenant reads are impossible.
    """
    if not x_user_id:
        raise HTTPException(status_code=403, detail="Missing X-User-Id header.")

    tenant_id = x_tenant_id or "default"
    registry: JobRegistry = http_request.app.state.job_registry
    job = registry.get_validated(job_id, tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found or access denied.")

    return StreamingResponse(
        registry.stream(job_id, tenant_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
