"""
Ingestion Service — FastAPI application.

Endpoints:
  POST /ingest              — trigger async ingestion of a document from MinIO
  POST /ingest/dataset      — load and ingest a HuggingFace sample dataset
  DELETE /vectors/{tid}/{did} — delete document vectors from Qdrant
  DELETE /vectors/{tid}     — delete all tenant vectors from Qdrant
  GET /health
  GET /metrics

Auth: all non-health endpoints require the gateway-injected X-User-Id header.
tenant_id is always taken from the gateway-injected X-Tenant-Id header (never
trusted from the request body) to prevent cross-tenant isolation bypass.
"""

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..adapters import HuggingFaceAdapter, MinIOAdapter, TextAdapter
from ..config import get_settings
from ..db import (
    ChunkRecord,
    delete_document_chunks,
    delete_tenant_chunks,
    persist_chunks,
    update_document_status,
)
from ..pipeline import invalidate_pipeline_cache, run_ingestion
from ..stores import delete_document_from_store, delete_tenant_from_store

logger = logging.getLogger(__name__)

_METRICS_ENABLED = False
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    from prometheus_client import Counter

    _METRICS_ENABLED = True
except ImportError:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Ingestion service starting up")
    yield
    logger.info("Ingestion service shutting down")


app = FastAPI(
    title="DocIntel Ingestion Service",
    version="1.0.0",
    docs_url="/docs",
    lifespan=lifespan,
)

# Instrumentator must be set up before the app starts (before first request),
# not inside the lifespan context manager.
if _METRICS_ENABLED:
    Instrumentator().instrument(app).expose(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


# =============================================================================
# Gateway auth dependency
# =============================================================================


def require_gateway_auth(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> str:
    """
    Reject requests that bypass the API Gateway.

    The gateway sets X-User-Id on every authenticated request.
    If it is missing, the caller reached the service directly (bypassing JWT
    validation and OPA authz) — reject with 403.
    """
    if not x_user_id:
        raise HTTPException(
            status_code=403,
            detail="Missing X-User-Id header. All requests must pass through the API Gateway.",
        )
    return x_user_id


def get_tenant_id(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> str:
    """
    Return tenant_id from gateway-injected header.

    The gateway always extracts tenant_id from the JWT and sets X-Tenant-Id.
    Request body tenant_id fields are ignored to prevent cross-tenant bypass.
    """
    return x_tenant_id or "default"


GatewayUserDep = Annotated[str, Depends(require_gateway_auth)]
TenantDep = Annotated[str, Depends(get_tenant_id)]


# =============================================================================
# Request / Response models
# =============================================================================


class IngestRequest(BaseModel):
    document_id: str
    # tenant_id kept for backward compat but overridden by X-Tenant-Id header
    tenant_id: str = Field(default="default")
    bucket: str
    object_path: str
    filename: str = Field(default="document")
    domain_hint: str = Field(default="auto")
    metadata: dict = Field(default_factory=dict)


class IngestResponse(BaseModel):
    status: str
    document_id: str


class DatasetIngestRequest(BaseModel):
    dataset_key: str  # "techqa" | "hr_policies" | "cuad"
    # tenant_id kept for backward compat but overridden by X-Tenant-Id header
    tenant_id: str = Field(default="default")
    samples: int = Field(default=10, ge=1, le=200)
    domain_hint: str = Field(default="auto")


class DatasetIngestResponse(BaseModel):
    status: str
    dataset_key: str
    tenant_id: str
    files_processed: int
    total_chunks: int
    domain: str


class BulkDatasetIngestRequest(BaseModel):
    """Matches the web-UI payload from the Documents page."""
    datasets: list[str] = Field(default_factory=list)
    # -1 or large values are treated as "all available" by the pipeline
    samples_per_dataset: int = Field(default=100, ge=-1)
    # tenant_id kept for backward compat but overridden by X-Tenant-Id header
    tenant_id: str = Field(default="default")


class BulkDatasetIngestResponse(BaseModel):
    total_indexed: int
    loaded: list[str]
    failed: list[str] = Field(default_factory=list)


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


async def _ingest_document_background(request: IngestRequest, effective_tenant_id: str) -> None:
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
            None,  # use default settings
        )

        # Persist chunks to PG
        chunk_records = [
            ChunkRecord(
                chunk_id=c["chunk_id"],
                document_id=request.document_id,
                tenant_id=effective_tenant_id,
                content=c["content"],
                chunk_index=c["chunk_index"],
                start_char=c["start_char"],
                end_char=c["end_char"],
                token_count=c["token_count"],
                metadata=c["metadata"],
            )
            for c in result["chunks"]
        ]
        persist_chunks(chunk_records)

        update_document_status(
            document_id=request.document_id,
            status="COMPLETED",
            chunk_count=result["chunk_count"],
        )

        logger.info(
            "Ingestion complete: document_id=%s chunks=%d domain=%s",
            request.document_id,
            result["chunk_count"],
            result["domain"],
        )

    except Exception as e:
        logger.exception("Ingestion failed for document %s", request.document_id)
        update_document_status(
            document_id=request.document_id,
            status="FAILED",
            error_message=str(e),
        )
    finally:
        for p in tmp_paths:
            try:
                shutil.rmtree(p.parent, ignore_errors=True)
            except Exception:
                pass


async def _ingest_dataset_background(dataset_key: str, tenant_id: str, samples: int, domain_hint: str) -> dict:
    """
    Dataset ingestion pipeline:
      1. HuggingFaceAdapter → local .txt files
      2. Stage 1 FileTypeRouter routes .txt → TextFileToDocument (bypasses Docling)
      3. Stage 2: BM25 + Ollama embed → Qdrant write
      4. Persist chunks to PG
    """
    adapter = HuggingFaceAdapter()
    tmp_paths: list[Path] = []

    try:
        tmp_paths = await adapter.fetch(
            {
                "dataset_key": dataset_key,
                "samples": samples,
                "tenant_id": tenant_id,
            }
        )

        if not tmp_paths:
            return {"files": 0, "chunks": 0, "domain": "general"}

        loop = asyncio.get_running_loop()
        total_chunks = 0
        domain = domain_hint
        all_chunk_records: list[ChunkRecord] = []

        for i, file_path in enumerate(tmp_paths):
            doc_id = f"sample_{dataset_key}_{i}"
            result = await loop.run_in_executor(
                None,
                run_ingestion,
                [file_path],
                doc_id,
                tenant_id,
                file_path.name,
                domain_hint,
                {"source": "sample_dataset", "dataset_key": dataset_key},
                None,
            )

            if domain == "auto":
                domain = result["domain"]

            for c in result["chunks"]:
                all_chunk_records.append(
                    ChunkRecord(
                        chunk_id=c["chunk_id"],
                        document_id=doc_id,
                        tenant_id=tenant_id,
                        content=c["content"],
                        chunk_index=c["chunk_index"],
                        start_char=c["start_char"],
                        end_char=c["end_char"],
                        token_count=c["token_count"],
                        metadata=c["metadata"],
                    )
                )
            total_chunks += result["chunk_count"]

        persist_chunks(all_chunk_records)

        return {"files": len(tmp_paths), "chunks": total_chunks, "domain": domain}

    finally:
        if tmp_paths:
            try:
                shutil.rmtree(tmp_paths[0].parent, ignore_errors=True)
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
):
    """
    Accept a document ingestion job and process it asynchronously.

    document-service uploads the file to MinIO and then calls this endpoint
    with the bucket + object_path. Returns 202 Accepted immediately; the
    ingestion-service updates document status in PG on completion.

    tenant_id is taken from the gateway-injected X-Tenant-Id header — the
    request body's tenant_id field is ignored to prevent tenant isolation bypass.
    """
    logger.info(
        "Ingestion job accepted: document_id=%s tenant=%s file=%s",
        request.document_id,
        tenant_id,
        request.filename,
    )
    background_tasks.add_task(_ingest_document_background, request, tenant_id)
    return IngestResponse(status="accepted", document_id=request.document_id)


@app.post("/ingest/dataset", response_model=DatasetIngestResponse)
async def ingest_dataset(
    request: DatasetIngestRequest,
    _user_id: GatewayUserDep,
    tenant_id: TenantDep,
):
    """
    Load a HuggingFace sample dataset and ingest it through the same Docling pipeline.
    """
    logger.info(
        "Dataset ingestion started: key=%s samples=%d tenant=%s",
        request.dataset_key,
        request.samples,
        tenant_id,
    )

    try:
        result = await _ingest_dataset_background(
            dataset_key=request.dataset_key,
            tenant_id=tenant_id,
            samples=request.samples,
            domain_hint=request.domain_hint,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Dataset ingestion failed for %s", request.dataset_key)
        raise HTTPException(status_code=500, detail=str(e))

    return DatasetIngestResponse(
        status="completed",
        dataset_key=request.dataset_key,
        tenant_id=tenant_id,
        files_processed=result["files"],
        total_chunks=result["chunks"],
        domain=result["domain"],
    )


_AVAILABLE_DATASETS = [
    {
        "key": "techqa",
        "name": "TechQA",
        "domain": "technical",
        "description": "Technical documentation Q&A pairs",
    },
    {
        "key": "hr_policies",
        "name": "HR Policies",
        "domain": "hr_policy",
        "description": "HR policy Q&A pairs",
    },
    {
        "key": "cuad",
        "name": "Legal Cases",
        "domain": "contracts",
        "description": "European Court legal cases (CUAD)",
    },
]


@app.get("/ingest/dataset")
async def list_datasets(_user_id: GatewayUserDep):
    """Return the list of available sample datasets. Consumed by the web-UI Documents page."""
    return {"available_datasets": _AVAILABLE_DATASETS}


@app.post("/ingest/dataset/load", response_model=BulkDatasetIngestResponse)
async def bulk_ingest_datasets(
    request: BulkDatasetIngestRequest,
    _user_id: GatewayUserDep,
    tenant_id: TenantDep,
):
    """
    Bulk-load one or more HuggingFace sample datasets.

    Called by the web-UI Documents page. Processes each dataset sequentially
    through the same Docling ingestion pipeline as real documents.
    """
    if not request.datasets:
        raise HTTPException(status_code=400, detail="No datasets specified")

    loaded: list[str] = []
    failed: list[str] = []
    total_chunks = 0

    for dataset_key in request.datasets:
        try:
            result = await _ingest_dataset_background(
                dataset_key=dataset_key,
                tenant_id=tenant_id,
                samples=request.samples_per_dataset,
                domain_hint="auto",
            )
            total_chunks += result["chunks"]
            loaded.append(dataset_key)
        except Exception:
            logger.exception("Bulk dataset ingestion failed for %s", dataset_key)
            failed.append(dataset_key)

    if not loaded:
        raise HTTPException(status_code=500, detail=f"All datasets failed: {failed}")

    return BulkDatasetIngestResponse(
        total_indexed=total_chunks,
        loaded=loaded,
        failed=failed,
    )


@app.delete("/vectors/{tenant_id}/{document_id}", response_model=VectorDeleteResponse)
async def delete_document_vectors(
    tenant_id: str,
    document_id: str,
    _user_id: GatewayUserDep,
    header_tenant_id: TenantDep,
):
    """Delete all Qdrant vectors for a specific document."""
    # Validate path tenant_id matches gateway-injected header to prevent cross-tenant deletion
    if header_tenant_id != "default" and tenant_id != header_tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete vectors for tenant '{tenant_id}': caller is tenant '{header_tenant_id}'.",
        )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, delete_document_from_store, tenant_id, document_id
    )
    await loop.run_in_executor(None, delete_document_chunks, document_id)
    return VectorDeleteResponse(deleted=True, document_id=document_id)


@app.delete("/vectors/{tenant_id}", response_model=VectorDeleteResponse)
async def delete_tenant_vectors(
    tenant_id: str,
    _user_id: GatewayUserDep,
    header_tenant_id: TenantDep,
):
    """Delete all Qdrant vectors and PG chunks for an entire tenant."""
    # Validate path tenant_id matches gateway-injected header
    if header_tenant_id != "default" and tenant_id != header_tenant_id:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete vectors for tenant '{tenant_id}': caller is tenant '{header_tenant_id}'.",
        )
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, delete_tenant_from_store, tenant_id)
    await loop.run_in_executor(None, delete_tenant_chunks, tenant_id)
    invalidate_pipeline_cache(tenant_id)
    return VectorDeleteResponse(deleted=True, tenant_id=tenant_id)
