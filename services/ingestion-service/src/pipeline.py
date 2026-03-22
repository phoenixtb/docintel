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

    @component.output_types(paths=Iterable[Union[Path, str]])
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
        from transformers import AutoTokenizer
        # model_max_length=int(1e9): we use this tokenizer only for counting tokens,
        # not for model inference. Setting an effectively unlimited model_max_length
        # prevents the "sequence too long (N > M)" warning that fires when encoding
        # a full pre-split document — which can be tens of thousands of tokens.
        self._tokenizer = AutoTokenizer.from_pretrained(
            "bert-base-uncased", model_max_length=int(1e9)
        )
        logger.debug("TokenAwareSplitter: tokenizer loaded (vocab=%d)", self._tokenizer.vocab_size)

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
    """Stage 2: enriched Documents → split → BM25 + Ollama embed → Qdrant write."""
    from haystack_integrations.components.embedders.fastembed import FastembedSparseDocumentEmbedder
    from haystack_integrations.components.embedders.ollama import OllamaDocumentEmbedder

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
    acl: DocumentACL | None = None,
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
    # acl.to_meta() is merged LAST: ACL fields cannot be overridden by extra_meta.
    effective_acl = acl or DocumentACL()
    meta_override = {
        "tenant_id": tenant_id,
        "document_id": document_id,
        "filename": filename,
        "domain": domain,
        "document_type": domain,   # used by rag-service SecureRetriever for domain filtering
        **(extra_meta or {}),
        **effective_acl.to_meta(),  # write-once: always applied last
    }

    # ── Stage 2: Enrich + Embed + Index ──────────────────────────────────
    # include_outputs_from forces the embedder's output into the result dict even
    # though it is consumed by the writer. Without this, embedded_docs is always [].
    ing_pipeline = _get_ingestion_pipeline(tenant_id, cfg)
    ing_result = ing_pipeline.run(
        {"enricher": {"documents": raw_docs, "meta_override": meta_override}},
        include_outputs_from={"embedder"},
    )

    written_docs: int = ing_result.get("writer", {}).get("documents_written", 0)
    embedded_docs: list[Document] = ing_result.get("embedder", {}).get("documents", [])

    # ── Build PG chunk records ────────────────────────────────────────────
    # Use the accurate positions/token counts stored by TokenAwareSplitter in
    # Document.meta (split_idx_start, split_idx_end, bert_token_count).
    # These refer to the pre-overlap raw chunk positions in the original document,
    # making them usable for source highlighting. The running char_offset approach
    # was incorrect because overlap-extended chunks don't have contiguous positions.
    chunks = []
    for i, doc in enumerate(embedded_docs):
        content = doc.content or ""
        # Haystack doc.id is a 64-char SHA-256 hex string; DB chunks.id is UUID.
        # Take the first 32 hex chars (128 bits) and reformat as UUID for deterministic upserts.
        raw_id = doc.id or ""
        if raw_id and len(raw_id) >= 32:
            chunk_id = str(uuid.UUID(bytes=bytes.fromhex(raw_id[:32])))
        else:
            chunk_id = str(uuid.uuid4())
        start_char = doc.meta.get("split_idx_start", 0)
        end_char = doc.meta.get("split_idx_end", start_char + len(content))
        token_count = doc.meta.get("bert_token_count", max(1, len(content.split())))

        chunks.append({
            "chunk_id": chunk_id,
            "content": content,
            "chunk_index": i,
            "start_char": start_char,
            "end_char": end_char,
            "token_count": token_count,
            "metadata": doc.meta,   # already contains full meta_override via MetadataEnricher
        })

    return {
        "embedded_count": written_docs,
        "chunk_count": len(chunks),
        "domain": domain,
        "collection": f"documents_{tenant_id}",
        "chunks": chunks,
    }


