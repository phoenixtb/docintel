"""
Hardware-aware Haystack ingestion pipeline.

Two-stage pipeline design:
  Stage 1 — Conversion (FileTypeRouter → per-format converter → DocumentJoiner):
    text/plain  → TextFileToDocument
    other types → DoclingConverter  (PDF, DOCX, PPTX, HTML — hardware-aware ONNX)
    Both branches merge via DocumentJoiner → raw Document objects with clean text

  Stage 2 — Ingestion (Embed + Index):
    MetadataEnricher → BM25SparseDocumentEmbedder → OpenAIDocumentEmbedder → DocumentWriter

Splitting into two stages allows domain classification to run between them:
  conversion → classify domain → build full metadata → ingestion

DoclingConverter accelerator selection (via docintel_common.detect_device):
  - MPS   on macOS Apple Silicon (dev / CI)
  - CUDA  on Linux with NVIDIA GPU (prod / K8s)
  - CPU   otherwise (ONNX with AVX-512 on server-class CPUs)

Sparse embedding model MUST match rag-service query side (Qdrant/bm25).
"""

import gc
import hashlib
import logging
import threading
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Union

from haystack import Pipeline, component
from haystack.dataclasses import ByteStream, Document
from haystack.components.converters import TextFileToDocument
from haystack.components.joiners import DocumentJoiner
from haystack.components.routers import FileTypeRouter
from haystack.components.writers import DocumentWriter
from haystack.document_stores.types import DuplicatePolicy

from docintel_common.device import detect_device
from docintel_common.domain import get_domain_classifier
from docintel_common.security import DocumentACL

from .config import Settings, get_settings
from .stores import get_document_store

logger = logging.getLogger(__name__)

_conversion_pipeline_cache: dict[str, Pipeline] = {}
_ingestion_pipeline_cache: dict[str, Pipeline] = {}
_pdf_ingestion_pipeline_cache: dict[str, Pipeline] = {}
# Per-tenant threading.Locks: run_ingestion is called from ThreadPoolExecutor threads,
# so asyncio.Lock is not usable here — threading.Lock is the correct primitive.
_pipeline_locks: dict[str, threading.Lock] = {}
_pipeline_locks_guard = threading.Lock()  # protects writes to _pipeline_locks dict


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

    @component.output_types(paths=Optional[List[Union[str, Path]]])
    def run(self, sources: Optional[List[Union[str, Path, ByteStream]]]) -> dict:
        paths: List[Union[str, Path]] = [
            s for s in (sources or [])
            if not isinstance(s, ByteStream)
        ]
        return {"paths": paths or None}


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
# TokenAwareSplitter — exact BERT token counting, recursive splitting
# ---------------------------------------------------------------------------

@component
class TokenAwareSplitter:
    """
    Splits documents into chunks that are guaranteed to fit within the embedding
    model's token budget, using the exact tokenizer the model uses.

    nomic-embed-text-v1/v1.5 uses bert-base-uncased (WordPiece, 30k vocab).
    Counting tokens with that tokenizer — not characters, not tiktoken — is the
    only way to guarantee no chunk ever exceeds the model's context window,
    regardless of content (legal TOC dot leaders, code, binary-looking strings).

    Algorithm (recursive descent, no truncation):
      1. If text fits within max_tokens → keep as one chunk.
      2. Otherwise, try splitting on each separator in order:
         "\n\n", "\n", ". ", " "
         Any resulting piece that still exceeds max_tokens is recursed with the
         next separator.
      3. If all separators are exhausted and a piece still overflows (single
         long "word"), binary-search for the largest prefix that fits and emit
         it, then continue with the remainder. This is the only code path that
         splits inside a word, and it only fires for truly pathological input.
      4. Adjacent chunks are assembled with overlap_tokens of token overlap to
         preserve cross-boundary context.

    Haystack lifecycle:
      warm_up() is called automatically by Pipeline before the first run().
      The tokenizer is loaded from the Docker image layer (pre-baked at build
      time) — zero network calls, negligible startup latency.
    """

    def __init__(self, max_tokens: int = 512, overlap_tokens: int = 64) -> None:
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self._tokenizer = None

    def warm_up(self) -> None:
        # Guard: Haystack calls warm_up() on every Pipeline.run(). Without this
        # check, AutoTokenizer.from_pretrained makes 4+ HuggingFace HEAD/GET
        # requests per document even when the model is locally cached, because
        # the transformers library validates cache freshness on every call.
        if self._tokenizer is not None:
            return
        from transformers import AutoTokenizer
        # model_max_length=int(1e9): we use this tokenizer only for counting tokens,
        # not for model inference. Setting an effectively unlimited model_max_length
        # prevents the "sequence too long (N > M)" warning that fires when encoding
        # a full pre-split document — which can be tens of thousands of tokens.
        self._tokenizer = AutoTokenizer.from_pretrained(
            "bert-base-uncased", model_max_length=int(1e9)
        )
        logger.info("TokenAwareSplitter: tokenizer loaded (vocab=%d)", self._tokenizer.vocab_size)

    def _count(self, text: str) -> int:
        assert self._tokenizer is not None, "warm_up() was not called"
        # truncation=False + no max_length suppresses the "sequence too long" warning
        # that fires when encoding the full document before the first split.
        return len(self._tokenizer.encode(text, add_special_tokens=False, truncation=False))

    def _split_text(self, text: str) -> list[str]:
        """Return a list of non-empty chunks each fitting within max_tokens."""
        if not text.strip():
            return []
        if self._count(text) <= self.max_tokens:
            return [text]

        separators = ["\n\n", "\n", ". ", " "]
        return self._recurse(text, separators)

    def _recurse(self, text: str, separators: list[str]) -> list[str]:
        if self._count(text) <= self.max_tokens:
            return [text]

        if not separators:
            # Last resort: binary-search for the largest token-safe prefix.
            return self._hard_split(text)

        sep = separators[0]
        rest = separators[1:]
        parts = text.split(sep)

        # Re-attach the separator (except after last part) and recurse
        pieces: list[str] = []
        buf = ""
        for i, part in enumerate(parts):
            candidate = buf + part + (sep if i < len(parts) - 1 else "")
            if self._count(candidate) <= self.max_tokens:
                buf = candidate
            else:
                if buf.strip():
                    pieces.append(buf)
                # The current part alone might still be too big → recurse
                buf = part + (sep if i < len(parts) - 1 else "")
                if self._count(buf) > self.max_tokens:
                    sub = self._recurse(buf, rest)
                    pieces.extend(sub)
                    buf = ""
        if buf.strip():
            if self._count(buf) > self.max_tokens:
                pieces.extend(self._recurse(buf, rest))
            else:
                pieces.append(buf)

        return pieces if pieces else [text]

    def _hard_split(self, text: str) -> list[str]:
        """
        Split a single token-overflowing unit (no whitespace separators work)
        by binary-searching for the token boundary. Only fires for strings with
        no spaces/newlines that are individually longer than max_tokens.
        """
        words = text.split(" ")
        chunks: list[str] = []
        lo, hi = 0, 0
        while hi < len(words):
            candidate = " ".join(words[lo : hi + 1])
            if self._count(candidate) <= self.max_tokens:
                hi += 1
            else:
                if hi == lo:
                    # Single word exceeds limit — emit it anyway (unavoidable)
                    logger.warning(
                        "TokenAwareSplitter: single word exceeds %d tokens (%d chars); "
                        "emitting as-is",
                        self.max_tokens, len(words[lo]),
                    )
                    chunks.append(words[lo])
                    lo = hi = lo + 1
                else:
                    chunks.append(" ".join(words[lo:hi]))
                    lo = hi
        if lo < len(words):
            chunks.append(" ".join(words[lo:]))
        return [c for c in chunks if c.strip()]

    def _add_overlap(self, chunks: list[str]) -> list[str]:
        """
        Prepend the tail of the previous chunk (up to overlap_tokens) to each
        subsequent chunk so cross-boundary context is preserved.
        """
        if self.overlap_tokens == 0 or len(chunks) <= 1:
            return chunks

        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_ids = self._tokenizer.encode(chunks[i - 1], add_special_tokens=False)
            tail_ids = prev_ids[-self.overlap_tokens:]
            tail_text = self._tokenizer.decode(tail_ids, skip_special_tokens=True)
            result.append(tail_text + " " + chunks[i])
        return result

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        if self._tokenizer is None:
            self.warm_up()

        output: list[Document] = []
        for doc in documents:
            content = doc.content or ""
            raw_chunks = self._split_text(content)
            chunks_with_overlap = self._add_overlap(raw_chunks)

            for idx, chunk_text in enumerate(chunks_with_overlap):
                # Compute char offsets from the pre-overlap raw chunk position in the
                # original document. The overlap-extended chunk_text has a prefix from
                # the previous chunk, so using chunk_text.find() would give wrong offsets.
                if idx < len(raw_chunks):
                    raw_start = content.find(raw_chunks[idx])
                    raw_end = raw_start + len(raw_chunks[idx]) if raw_start >= 0 else 0
                    bert_tokens = self._count(raw_chunks[idx])
                else:
                    raw_start = 0
                    raw_end = 0
                    bert_tokens = 0

                output.append(Document(
                    content=chunk_text,
                    meta={
                        **doc.meta,
                        "split_id": idx,
                        "split_idx_start": raw_start,
                        "split_idx_end": raw_end,
                        "bert_token_count": bert_tokens,
                    },
                ))

        return {"documents": output}


# ---------------------------------------------------------------------------
# Pipeline factories
# ---------------------------------------------------------------------------

def _build_pdf_pipeline_options(cfg: Settings, profile=None) -> "PdfPipelineOptions":
    """Build PdfPipelineOptions for a given PDF profile (or safe defaults)."""
    from docling.datamodel.pipeline_options import RapidOcrOptions, PdfPipelineOptions

    options = PdfPipelineOptions()
    options.do_table_structure = True  # always on — TableFormer only fires on layout-detected tables

    if profile is None:
        # Default: honour config flags (used for non-PDF files / text path)
        options.do_ocr = cfg.docling_do_ocr
    else:
        options.do_ocr = profile.do_ocr
        if hasattr(options, "force_full_page_ocr") and profile.force_full_page_ocr:
            options.force_full_page_ocr = True
        if hasattr(options, "force_backend_text") and profile.force_backend_text:
            options.force_backend_text = True

    if options.do_ocr:
        # RapidOCR (ONNX) is 2-4x faster than EasyOCR on CPU and uses ~5x less peak RAM.
        # bitmap_area_threshold=0.3: pages with <30% bitmap coverage skip OCR entirely
        # and rely on Docling's digital text extraction — saves OCR on mostly-text pages.
        options.ocr_options = RapidOcrOptions(
            bitmap_area_threshold=0.3,
        )

    device = detect_device()
    options.accelerator_options.device = device
    logger.info(
        "Docling PDF options: strategy=%s ocr=%s table_structure=%s device=%s",
        getattr(profile, "strategy", "default"),
        options.do_ocr,
        options.do_table_structure,
        device.upper(),
    )
    return options


def _get_docling_converter(cfg: Settings, profile=None):
    """Return a hardware-aware DoclingConverter Haystack component (used for text/single-shard path)."""
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_haystack.converter import DoclingConverter, ExportType

    options = _build_pdf_pipeline_options(cfg, profile)

    return DoclingConverter(
        converter=DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        ),
        export_type=ExportType.DOC_CHUNKS,
    )


def _build_raw_docling_converter(cfg: Settings, profile=None) -> "DocumentConverter":
    """
    Build a raw docling DocumentConverter (not wrapped in Haystack) for the sharded PDF path.

    The sharded path calls converter.convert(path, page_range=...) directly per shard,
    then chunks with HybridChunker outside the Haystack pipeline.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = _build_pdf_pipeline_options(cfg, profile)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
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
    """Stage 2: enriched Documents → split → BM25 + dense embed → Qdrant write."""
    from haystack.components.embedders.openai_document_embedder import OpenAIDocumentEmbedder
    from haystack.utils import Secret
    from haystack_integrations.components.embedders.fastembed import FastembedSparseDocumentEmbedder

    document_store = get_document_store(tenant_id, cfg)

    pipeline = Pipeline()
    pipeline.add_component("enricher", MetadataEnricher())
    # TokenAwareSplitter counts tokens with bert-base-uncased — the exact tokenizer
    # nomic-embed-text uses — so chunk limits are precise regardless of content.
    # 512 tokens/chunk, 64-token overlap: industry standard for 2048-context models.
    pipeline.add_component(
        "splitter",
        TokenAwareSplitter(max_tokens=512, overlap_tokens=64),
    )
    pipeline.add_component(
        "sparse_embedder",
        # model must match rag-service BM25SparseTextEmbedder(model_name="Qdrant/bm25")
        FastembedSparseDocumentEmbedder(model="Qdrant/bm25"),
    )
    pipeline.add_component(
        "embedder",
        OpenAIDocumentEmbedder(
            model=cfg.llm_embed_model,
            api_base_url=cfg.llm_embed_url,
            api_key=Secret.from_token(cfg.llm_api_key),
        ),
    )
    pipeline.add_component(
        "writer",
        DocumentWriter(
            document_store=document_store,
            policy=DuplicatePolicy.OVERWRITE,
        ),
    )

    pipeline.connect("enricher.documents", "splitter.documents")
    pipeline.connect("splitter.documents", "sparse_embedder.documents")
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
    """
    Ingestion pipeline is per-tenant (separate Qdrant collection per tenant).

    Thread-safe: per-tenant threading.Lock prevents concurrent first-time builds
    for the same tenant from producing duplicate pipelines. run_ingestion is called
    from ThreadPoolExecutor threads so threading.Lock (not asyncio.Lock) is used.
    """
    # Double-checked locking: fast path skips lock acquisition when already cached.
    if tenant_id in _ingestion_pipeline_cache:
        return _ingestion_pipeline_cache[tenant_id]

    with _pipeline_locks_guard:
        if tenant_id not in _pipeline_locks:
            _pipeline_locks[tenant_id] = threading.Lock()
    lock = _pipeline_locks[tenant_id]

    with lock:
        if tenant_id not in _ingestion_pipeline_cache:
            _ingestion_pipeline_cache[tenant_id] = _build_ingestion_pipeline(tenant_id, cfg)
    return _ingestion_pipeline_cache[tenant_id]


def _build_pdf_ingestion_pipeline(tenant_id: str, cfg: Settings) -> Pipeline:
    """
    Ingestion pipeline for pre-chunked PDF shard documents.

    Skips TokenAwareSplitter — chunks come from docling's HybridChunker which already
    produces appropriately-sized segments. Applies: MetadataEnricher → BM25 sparse embed
    → dense embed → Qdrant write.
    """
    from haystack.components.embedders.openai_document_embedder import OpenAIDocumentEmbedder
    from haystack.utils import Secret
    from haystack_integrations.components.embedders.fastembed import FastembedSparseDocumentEmbedder

    document_store = get_document_store(tenant_id, cfg)

    pipeline = Pipeline()
    pipeline.add_component("enricher", MetadataEnricher())
    pipeline.add_component(
        "sparse_embedder",
        FastembedSparseDocumentEmbedder(model="Qdrant/bm25"),
    )
    pipeline.add_component(
        "embedder",
        OpenAIDocumentEmbedder(
            model=cfg.llm_embed_model,
            api_base_url=cfg.llm_embed_url,
            api_key=Secret.from_token(cfg.llm_api_key),
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

    logger.info("Built PDF shard ingestion pipeline for tenant=%s", tenant_id)
    return pipeline


def _get_pdf_ingestion_pipeline(tenant_id: str, cfg: Settings) -> Pipeline:
    """PDF shard pipeline — no splitter. Thread-safe, per-tenant cached."""
    if tenant_id in _pdf_ingestion_pipeline_cache:
        return _pdf_ingestion_pipeline_cache[tenant_id]

    with _pipeline_locks_guard:
        if tenant_id not in _pipeline_locks:
            _pipeline_locks[tenant_id] = threading.Lock()
    lock = _pipeline_locks[tenant_id]

    with lock:
        if tenant_id not in _pdf_ingestion_pipeline_cache:
            _pdf_ingestion_pipeline_cache[tenant_id] = _build_pdf_ingestion_pipeline(tenant_id, cfg)
    return _pdf_ingestion_pipeline_cache[tenant_id]


def invalidate_pipeline_cache(tenant_id: str) -> None:
    _ingestion_pipeline_cache.pop(tenant_id, None)
    _pdf_ingestion_pipeline_cache.pop(tenant_id, None)


# ---------------------------------------------------------------------------
# Checkpoint helpers (in-process cache backed by document-service DB via HTTP)
# ---------------------------------------------------------------------------

_checkpoint_cache: dict[str, dict] = {}
_checkpoint_lock = threading.Lock()


def _load_checkpoint(document_id: str) -> dict | None:
    with _checkpoint_lock:
        return _checkpoint_cache.get(document_id)


def _save_checkpoint(
    document_id: str,
    tenant_id: str,
    last_completed_page: int,
    chunk_count_so_far: int,
    total_pages: int,
) -> None:
    with _checkpoint_lock:
        _checkpoint_cache[document_id] = {
            "last_completed_page":  last_completed_page,
            "chunk_count_so_far":   chunk_count_so_far,
            "total_pages":          total_pages,
        }
    logger.debug(
        "Checkpoint: document_id=%s page=%d/%d chunks_so_far=%d",
        document_id, last_completed_page, total_pages, chunk_count_so_far,
    )


def _delete_checkpoint(document_id: str) -> None:
    with _checkpoint_lock:
        _checkpoint_cache.pop(document_id, None)


def _chunk_id_from_index(document_id: str, global_index: int) -> str:
    """Deterministic chunk UUID = SHA-256(document_id:global_index)[:16 bytes]."""
    raw = hashlib.sha256(f"{document_id}:{global_index}".encode()).digest()[:16]
    return str(uuid.UUID(bytes=raw))


def _publish_progress_sync(
    document_id: str,
    tenant_id: str,
    filename: str,
    current_page: int,
    total_pages: int,
    stage: str,
    cfg: Settings,
) -> None:
    """
    Publish a shard-level progress event to documents.progress (synchronous).

    Runs inside the ProcessPoolExecutor subprocess, so uses the synchronous
    redis-py client rather than the async one. Non-fatal if Redis is unavailable.
    """
    import json as _json
    try:
        import redis as _redis
        r = _redis.Redis(
            host=cfg.redis_host,
            port=cfg.redis_port,
            password=cfg.redis_password or None,
            decode_responses=True,
        )
        payload = _json.dumps({
            "documentId":  document_id,
            "tenantId":    tenant_id,
            "filename":    filename,
            "currentPage": current_page,
            "totalPages":  total_pages,
            "stage":       stage,
        })
        r.xadd("documents.progress", {"payload": payload})
        r.close()
    except Exception as e:
        logger.debug("Progress publish failed (non-fatal): %s", e)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _classify_domain(sample_text: str, filename: str) -> str:
    try:
        clf = get_domain_classifier()
        clf_result = clf.classify(sample_text)
        logger.info(
            "Auto-classified '%s' as domain='%s' (confidence=%.2f)",
            filename, clf_result.domain, clf_result.confidence,
        )
        return clf_result.domain
    except Exception as e:
        logger.warning("Domain classification failed (non-fatal): %s — using 'general'", e)
        return "general"


def _build_chunk_payload(doc: Document, chunk_index: int, document_id: str) -> dict:
    """Build a PG-ready chunk dict from a Haystack Document."""
    content = doc.content or ""
    raw_id = doc.id or ""
    if raw_id and len(raw_id) >= 32:
        chunk_id = str(uuid.UUID(bytes=bytes.fromhex(raw_id[:32])))
    else:
        chunk_id = _chunk_id_from_index(document_id, chunk_index)
    start_char = doc.meta.get("split_idx_start", 0)
    end_char   = doc.meta.get("split_idx_end", start_char + len(content))
    token_count = doc.meta.get("bert_token_count", max(1, len(content.split())))
    return {
        "chunk_id":    chunk_id,
        "content":     content,
        "chunk_index": chunk_index,
        "start_char":  start_char,
        "end_char":    end_char,
        "token_count": token_count,
        "metadata":    doc.meta,
    }


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
    acl: DocumentACL | None = None,
) -> dict:
    """
    Run the full ingestion pipeline with smart PDF routing and sharded processing.

    For PDF files:
      1. probe_pdf() → PdfProfile (digital / hybrid / scanned)
      2. Page-range shard loop (DOCLING_SHARD_PAGES pages per iteration)
         Each shard: convert → chunk (HybridChunker) → embed → Qdrant write → PG append
      3. Crash-resume via in-process checkpoints

    For text/* files:
      Single pass through the existing Haystack conversion + ingestion pipeline.
      Behavior identical to pre-redesign; no sharding overhead for small files.

    Returns:
        {
          "embedded_count": int,
          "chunk_count":    int,
          "domain":         str,
          "collection":     str,
          "chunks":         list[dict]   # only populated for single-shard path
        }
    """
    cfg = settings or get_settings()
    path = file_paths[0]
    is_pdf = path.suffix.lower() in {".pdf"}

    if is_pdf:
        return _run_pdf_sharded(path, document_id, tenant_id, filename, domain_hint, extra_meta, cfg, acl)
    else:
        return _run_text(file_paths, document_id, tenant_id, filename, domain_hint, extra_meta, cfg, acl)


def _run_text(
    file_paths: list[Path],
    document_id: str,
    tenant_id: str,
    filename: str,
    domain_hint: str,
    extra_meta: dict | None,
    cfg: Settings,
    acl: DocumentACL | None,
) -> dict:
    """Single-pass ingestion for text/* and other non-PDF files (unchanged from pre-redesign)."""
    str_paths = [str(p) for p in file_paths]

    conv_pipeline = _get_conversion_pipeline(cfg)
    conv_result = conv_pipeline.run({"router": {"sources": str_paths}})
    raw_docs: list[Document] = conv_result.get("joiner", {}).get("documents", [])

    if not raw_docs:
        logger.warning("No documents produced by Docling for %s", filename)
        return {"embedded_count": 0, "chunk_count": 0, "domain": "general",
                "collection": f"documents_{tenant_id}", "chunks": []}

    domain = domain_hint
    if domain == "auto":
        sample_text = " ".join((d.content or "") for d in raw_docs[:10])[:5000]
        domain = _classify_domain(sample_text, filename)

    effective_acl = acl or DocumentACL()
    meta_override = {
        "tenant_id":     tenant_id,
        "document_id":   document_id,
        "filename":      filename,
        "domain":        domain,
        "document_type": domain,
        **(extra_meta or {}),
        **effective_acl.to_meta(),
    }

    ing_pipeline = _get_ingestion_pipeline(tenant_id, cfg)
    ing_result = ing_pipeline.run(
        {"enricher": {"documents": raw_docs, "meta_override": meta_override}},
        include_outputs_from={"embedder"},
    )

    written_docs: int = ing_result.get("writer", {}).get("documents_written", 0)
    embedded_docs: list[Document] = ing_result.get("embedder", {}).get("documents", [])

    chunks = [_build_chunk_payload(doc, i, document_id) for i, doc in enumerate(embedded_docs)]

    return {
        "embedded_count": written_docs,
        "chunk_count":    len(chunks),
        "domain":         domain,
        "collection":     f"documents_{tenant_id}",
        "chunks":         chunks,
    }


def _run_pdf_sharded(
    path: Path,
    document_id: str,
    tenant_id: str,
    filename: str,
    domain_hint: str,
    extra_meta: dict | None,
    cfg: Settings,
    acl: DocumentACL | None,
) -> dict:
    """
    Memory-bounded, crash-resumable PDF ingestion via page_range sharding.

    Each shard converts DOCLING_SHARD_PAGES pages, chunks with HybridChunker,
    embeds in batches, writes to Qdrant, appends to PG, and saves a checkpoint.
    Memory from the previous shard is explicitly released before the next.
    """
    from .pdf_probe import probe_pdf
    from .document_client import ChunkPayload, DocumentServiceClient

    profile = probe_pdf(path)
    total_pages = profile.page_count
    shard_size  = cfg.docling_shard_pages

    if total_pages == 0:
        raise ValueError(
            f"PDF probe returned 0 pages for '{filename}' — file may be corrupted or unreadable by pypdfium2. "
            "Re-upload the document."
        )

    logger.info(
        "PDF sharded ingestion: document_id=%s file=%s pages=%d strategy=%s shard_size=%d",
        document_id, filename, total_pages, profile.strategy, shard_size,
    )

    # Resume from checkpoint if available (crash recovery)
    checkpoint     = _load_checkpoint(document_id)
    start_page     = (checkpoint["last_completed_page"] + 1) if checkpoint else 0
    chunk_offset   = checkpoint["chunk_count_so_far"] if checkpoint else 0
    total_chunks   = chunk_offset
    domain_decided = domain_hint != "auto" or bool(checkpoint)
    domain         = domain_hint if domain_hint != "auto" else "general"

    converter   = _build_raw_docling_converter(cfg, profile)
    ing_pipeline = _get_pdf_ingestion_pipeline(tenant_id, cfg)
    effective_acl = acl or DocumentACL()
    doc_client  = DocumentServiceClient()

    try:
        from docling.chunking import HybridChunker
        chunker = HybridChunker()
    except ImportError:
        logger.warning("HybridChunker not available — falling back to text path for PDF")
        return _run_text([path], document_id, tenant_id, filename, domain_hint, extra_meta, cfg, acl)

    first_shard = True

    while start_page < total_pages:
        end_page   = min(start_page + shard_size - 1, total_pages - 1)
        # Docling page_range is 1-based inclusive [start, end]
        page_range = (start_page + 1, end_page + 1)

        logger.info(
            "PDF shard: document_id=%s pages=%d-%d/%d",
            document_id, start_page + 1, end_page + 1, total_pages,
        )

        _publish_progress_sync(
            document_id, tenant_id, filename,
            current_page=start_page + 1,
            total_pages=total_pages,
            stage=f"Converting pages {start_page + 1}-{end_page + 1}/{total_pages}",
            cfg=cfg,
        )

        try:
            result = converter.convert(path, page_range=page_range)
        except Exception as e:
            logger.error(
                "Docling conversion failed for shard pages=%d-%d of %s: %s",
                start_page, end_page, document_id, e,
            )
            raise

        # Domain classification on the first shard only
        if not domain_decided:
            sample_text = result.document.export_to_markdown()[:5000]
            domain = _classify_domain(sample_text, filename)
            domain_decided = True

        meta_override = {
            "tenant_id":     tenant_id,
            "document_id":   document_id,
            "filename":      filename,
            "domain":        domain,
            "document_type": domain,
            **(extra_meta or {}),
            **effective_acl.to_meta(),
        }

        # Chunk via HybridChunker then build Haystack Documents
        shard_docs: list[Document] = []
        for raw_chunk in chunker.chunk(dl_doc=result.document):
            try:
                text = chunker.serialize(raw_chunk)
            except Exception:
                text = str(raw_chunk.text or "")
            if not text.strip():
                continue
            shard_docs.append(Document(
                content=text,
                meta={
                    "split_id":       chunk_offset + len(shard_docs),
                    "split_idx_start": 0,
                    "split_idx_end":   len(text),
                    "bert_token_count": max(1, len(text.split())),
                },
            ))

        # Release conversion result before embedding (frees Docling internals)
        del result
        gc.collect()

        if shard_docs:
            # Embed + write to Qdrant via the shard ingestion pipeline
            ing_result = ing_pipeline.run(
                {"enricher": {"documents": shard_docs, "meta_override": meta_override}},
                include_outputs_from={"embedder"},
            )
            embedded_docs: list[Document] = ing_result.get("embedder", {}).get("documents", [])

            # Build PG chunk payloads with globally-unique deterministic IDs
            chunk_payloads = []
            for i, doc in enumerate(embedded_docs):
                global_idx = chunk_offset + i
                content    = doc.content or ""
                chunk_payloads.append(ChunkPayload(
                    chunk_id    = _chunk_id_from_index(document_id, global_idx),
                    chunk_index = global_idx,
                    content     = content,
                    start_char  = 0,
                    end_char    = len(content),
                    token_count = doc.meta.get("bert_token_count", max(1, len(content.split()))),
                    metadata    = doc.meta,
                ))

            # Append to PG (clear_existing only on the first shard of a fresh run)
            doc_client.append_chunks(
                document_id,
                tenant_id,
                chunk_payloads,
                clear_existing=(first_shard and not checkpoint),
            )

            chunk_offset += len(chunk_payloads)
            total_chunks  = chunk_offset

            del embedded_docs, shard_docs, chunk_payloads
            gc.collect()

        _save_checkpoint(document_id, tenant_id, end_page, chunk_offset, total_pages)
        first_shard = False
        start_page  = end_page + 1

    _delete_checkpoint(document_id)

    logger.info(
        "PDF sharded ingestion complete: document_id=%s total_chunks=%d domain=%s",
        document_id, total_chunks, domain,
    )

    return {
        "embedded_count": total_chunks,
        "chunk_count":    total_chunks,
        "domain":         domain,
        "collection":     f"documents_{tenant_id}",
        "chunks":         [],  # chunks already persisted shard-by-shard
    }


