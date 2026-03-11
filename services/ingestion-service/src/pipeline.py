"""
Hardware-aware Haystack ingestion pipeline.

Two-stage pipeline design:
  Stage 1 — Conversion (FileTypeRouter → per-format converter → DocumentJoiner):
    text/plain  → TextFileToDocument
    other types → DoclingConverter  (PDF, DOCX, PPTX, HTML — hardware-aware ONNX)
    Both branches merge via DocumentJoiner → raw Document objects with clean text

  Stage 2 — Ingestion (Embed + Index):
    MetadataEnricher → BM25SparseDocumentEmbedder → OllamaDocumentEmbedder → DocumentWriter

Splitting into two stages allows domain classification to run between them:
  conversion → classify domain → build full metadata → ingestion

DoclingConverter accelerator selection (via docintel_common.detect_device):
  - MPS   on macOS Apple Silicon (dev / CI)
  - CUDA  on Linux with NVIDIA GPU (prod / K8s)
  - CPU   otherwise (ONNX with AVX-512 on server-class CPUs)

Sparse embedding model MUST match rag-service query side (Qdrant/bm25).
"""

import logging
import uuid
from pathlib import Path
from typing import List, Optional, Union

from haystack import Pipeline, component
from haystack.dataclasses import ByteStream, Document
from haystack.components.converters import TextFileToDocument
from haystack.components.joiners import DocumentJoiner
from haystack.components.routers import FileTypeRouter
from haystack.components.writers import DocumentWriter
from haystack.document_stores.types import DuplicatePolicy

from docintel_common.device import detect_device
from docintel_common.domain import get_domain_classifier

from .config import Settings, get_settings
from .stores import get_document_store

logger = logging.getLogger(__name__)

_conversion_pipeline_cache: dict[str, Pipeline] = {}
_ingestion_pipeline_cache: dict[str, Pipeline] = {}


# ---------------------------------------------------------------------------
# SourcesToPaths — adapter bridging FileTypeRouter → DoclingConverter
# ---------------------------------------------------------------------------

@component
class SourcesToPaths:
    """
    Adapts FileTypeRouter's `sources` output (Optional[List[str|Path|ByteStream]])
    to DoclingConverter's `paths` input (Iterable[str|Path]).

    DoclingConverter is a community component that predates the Haystack 2.x
    `sources` convention; this thin adapter keeps the pipeline fully declarative
    without forking the upstream package.

    ByteStream entries are dropped — DoclingConverter requires on-disk paths.
    """

    @component.output_types(paths=List[Union[str, Path]])
    def run(self, sources: Optional[List[Union[str, Path, ByteStream]]]) -> dict:
        paths: list[Union[str, Path]] = [
            s for s in (sources or [])
            if not isinstance(s, ByteStream)
        ]
        return {"paths": paths}


# ---------------------------------------------------------------------------
# MetadataEnricher — Haystack component
# ---------------------------------------------------------------------------

@component
class MetadataEnricher:
    """
    Merges document-level metadata (document_id, tenant_id, filename, domain, …)
    into every Document's .meta before embedding and indexing.

    This ensures Qdrant payloads contain all fields needed for:
      - Document deletion (filter by document_id)
      - Tenant isolation verification
      - Domain-scoped retrieval (document_type field)
      - Source attribution in responses (filename)
      - RBAC (allowed_roles)
    """

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document], meta_override: dict) -> dict:
        for doc in documents:
            doc.meta = {**doc.meta, **meta_override}
        return {"documents": documents}


# ---------------------------------------------------------------------------
# Pipeline factories
# ---------------------------------------------------------------------------

def _get_docling_converter(cfg: Settings):
    """Return a hardware-aware DoclingConverter instance."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter
    from docling_haystack.converter import DoclingConverter, ExportType

    options = PdfPipelineOptions()
    options.do_ocr = cfg.docling_do_ocr
    options.do_table_structure = cfg.docling_do_table_structure

    device = detect_device()
    options.accelerator_options.device = device
    logger.info(
        "Docling: using %s (ocr=%s, table_structure=%s)",
        device.upper(), options.do_ocr, options.do_table_structure,
    )

    return DoclingConverter(
        converter=DocumentConverter(format_options={InputFormat.PDF: options}),
        export_type=ExportType.DOC_CHUNKS,
    )


def _build_conversion_pipeline(cfg: Settings) -> Pipeline:
    """
    Stage 1: file paths → raw Haystack Documents.

    FileTypeRouter dispatches by MIME type:
      text/plain  → TextFileToDocument (no extraction needed, already clean text)
      unclassified → SourcesToPaths adapter → DoclingConverter
                     (PDF, DOCX, PPTX, HTML — structure-aware ONNX extraction)
    DocumentJoiner merges both branches into a single document list.
    """
    pipeline = Pipeline()
    pipeline.add_component(
        "router",
        FileTypeRouter(mime_types=["text/plain"]),
    )
    pipeline.add_component(
        "text_converter",
        TextFileToDocument(encoding="utf-8"),
    )
    pipeline.add_component("sources_to_paths", SourcesToPaths())
    pipeline.add_component("docling_converter", _get_docling_converter(cfg))
    pipeline.add_component("joiner", DocumentJoiner())

    pipeline.connect("router.text/plain", "text_converter.sources")
    pipeline.connect("router.unclassified", "sources_to_paths.sources")
    pipeline.connect("sources_to_paths.paths", "docling_converter.paths")
    pipeline.connect("text_converter.documents", "joiner.documents")
    pipeline.connect("docling_converter.documents", "joiner.documents")

    return pipeline


def _build_ingestion_pipeline(tenant_id: str, cfg: Settings) -> Pipeline:
    """Stage 2: enriched Documents → BM25 + Ollama embed → Qdrant write."""
    from haystack_integrations.components.embedders.fastembed import FastembedSparseDocumentEmbedder
    from haystack_integrations.components.embedders.ollama import OllamaDocumentEmbedder

    document_store = get_document_store(tenant_id, cfg)

    pipeline = Pipeline()
    pipeline.add_component("enricher", MetadataEnricher())
    pipeline.add_component(
        "sparse_embedder",
        # Must match rag-service BM25SparseTextEmbedder(model_name="Qdrant/bm25")
        FastembedSparseDocumentEmbedder(model="Qdrant/bm25"),
    )
    pipeline.add_component(
        "embedder",
        OllamaDocumentEmbedder(
            model=cfg.ollama_embedding_model,
            url=cfg.ollama_base_url,
        ),
    )
    pipeline.add_component(
        "writer",
        DocumentWriter(
            document_store=document_store,
            policy=DuplicatePolicy.OVERWRITE,
        ),
    )

    pipeline.connect("enricher.documents", "sparse_embedder.documents")
    pipeline.connect("sparse_embedder.documents", "embedder.documents")
    pipeline.connect("embedder.documents", "writer.documents")

    logger.info("Built ingestion pipeline for tenant=%s", tenant_id)
    return pipeline


def _get_conversion_pipeline(cfg: Settings) -> Pipeline:
    """Conversion pipeline is tenant-independent; cached globally."""
    key = "conversion"
    if key not in _conversion_pipeline_cache:
        _conversion_pipeline_cache[key] = _build_conversion_pipeline(cfg)
    return _conversion_pipeline_cache[key]


def _get_ingestion_pipeline(tenant_id: str, cfg: Settings) -> Pipeline:
    """Ingestion pipeline is per-tenant (separate Qdrant collection per tenant)."""
    if tenant_id not in _ingestion_pipeline_cache:
        _ingestion_pipeline_cache[tenant_id] = _build_ingestion_pipeline(tenant_id, cfg)
    return _ingestion_pipeline_cache[tenant_id]


def invalidate_pipeline_cache(tenant_id: str) -> None:
    _ingestion_pipeline_cache.pop(tenant_id, None)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_ingestion(
    file_paths: list[Path],
    document_id: str,
    tenant_id: str,
    filename: str,
    domain_hint: str = "auto",
    extra_meta: dict | None = None,
    settings: Settings | None = None,
) -> dict:
    """
    Run the full two-stage ingestion pipeline.

    Stage 1: Docling converts files → Documents with clean text/structure
    Classification: Domain auto-detected from content (if domain_hint == "auto")
    Stage 2: MetadataEnricher → BM25 sparse → Ollama dense → Qdrant write

    Returns:
        {
          "embedded_count": int,
          "chunk_count": int,
          "domain": str,
          "collection": str,
          "chunks": list[dict]   # for PostgreSQL persistence
        }
    """
    cfg = settings or get_settings()
    str_paths = [str(p) for p in file_paths]

    # ── Stage 1: Conversion ───────────────────────────────────────────────
    conv_pipeline = _get_conversion_pipeline(cfg)
    conv_result = conv_pipeline.run({"router": {"sources": str_paths}})
    raw_docs: list[Document] = conv_result.get("joiner", {}).get("documents", [])

    if not raw_docs:
        logger.warning("No documents produced by Docling for %s", filename)
        return {"embedded_count": 0, "chunk_count": 0, "domain": "general",
                "collection": f"documents_{tenant_id}", "chunks": []}

    # ── Domain classification ─────────────────────────────────────────────
    domain = domain_hint
    if domain == "auto":
        sample_text = " ".join((d.content or "") for d in raw_docs[:10])[:5000]
        try:
            clf = get_domain_classifier()
            clf_result = clf.classify(sample_text)
            domain = clf_result.domain
            logger.info(
                "Auto-classified '%s' as domain='%s' (confidence=%.2f)",
                filename, domain, clf_result.confidence,
            )
        except Exception as e:
            logger.warning("Domain classification failed (non-fatal): %s — using 'general'", e)
            domain = "general"

    # ── Build full document-level metadata ────────────────────────────────
    meta_override = {
        "tenant_id": tenant_id,
        "document_id": document_id,
        "filename": filename,
        "domain": domain,
        "document_type": domain,   # used by rag-service SecureRetriever for domain filtering
        **(extra_meta or {}),
    }

    # ── Stage 2: Enrich + Embed + Index ──────────────────────────────────
    ing_pipeline = _get_ingestion_pipeline(tenant_id, cfg)
    ing_result = ing_pipeline.run({
        "enricher": {"documents": raw_docs, "meta_override": meta_override},
    })

    written_docs: int = ing_result.get("writer", {}).get("documents_written", 0)
    embedded_docs: list[Document] = ing_result.get("embedder", {}).get("documents", [])

    # ── Build PG chunk records ────────────────────────────────────────────
    chunks = []
    char_offset = 0
    for i, doc in enumerate(embedded_docs):
        content = doc.content or ""
        end_char = char_offset + len(content)
        token_count = max(1, len(content.split()))
        chunk_id = doc.id or str(uuid.uuid4())

        chunks.append({
            "chunk_id": chunk_id,
            "content": content,
            "chunk_index": i,
            "start_char": char_offset,
            "end_char": end_char,
            "token_count": token_count,
            "metadata": doc.meta,   # already contains full meta_override via MetadataEnricher
        })
        char_offset = end_char + 1

    return {
        "embedded_count": written_docs,
        "chunk_count": len(chunks),
        "domain": domain,
        "collection": f"documents_{tenant_id}",
        "chunks": chunks,
    }


