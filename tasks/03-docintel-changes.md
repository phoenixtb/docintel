# 03 — DocIntel Changes

Target: cut all model serving out of DocIntel and route inference to LMForge.
Page-level routing decides per page whether digital extraction, lightweight CPU OCR, or VLM is the right tool — VLM is the **escalation tier, not the default**.

**Mac-first.** This doc is written for the M3 Pro 38 GB dev machine running LMForge locally on Apple Silicon (oMLX). A "Phase 2 — Proxmox" sub-section at the end covers what changes when the RTX 5060 box is online.

After these changes:
- `ingestion-service` peak RAM during a 200-page PDF: **< 800 MB** (was OOM at 2 GB+)
- No `torch`, `transformers`, `easyocr`, `rapidocr`, `sentence-transformers`, `onnxruntime` baked into images that don't need them
- No `infinity` sidecar
- All inference (chat, embed, rerank, VLM) flows through `http://host.docker.internal:11430` (LMForge on the Mac host)

## 0. Where this code lives

DocIntel root: `/Users/titasbiswas/projects/ai_focused/docintel`. All paths in this doc are relative to that root.

LMForge runs natively on the Mac host. Docker containers reach it via `host.docker.internal:11430` (already wired in `docker-compose.yml` via `extra_hosts`).

## 1. Current state

Loaded models in process today:

| Service | Model | RAM cost | Notes |
|---|---|---|---|
| `ingestion-service` | EasyOCR / RapidOCR det+rec | ~750 MB | OOM cause on big PDFs |
| `ingestion-service` | Docling layout (DETR) | ~280 MB | safetensors baked into image |
| `ingestion-service` | Docling TableFormer | ~70 MB | baked, only for table cells |
| `ingestion-service` | DeBERTa zero-shot domain classifier | ~440 MB | in-process |
| `ingestion-service` | bert-base-uncased tokenizer | ~110 MB | TokenAwareSplitter |
| `ingestion-service` | FastEmbed BM25 sparse | ~30 MB | trivial |
| `rag-service` | DeBERTa domain classifier | ~440 MB | shared code |
| `rag-service` | sentence-transformers cross-encoder | ~85 MB | or via Infinity |
| `infinity` (sidecar) | cross-encoder ONNX | ~150 MB | optional |

Already remote today via `LLM_CHAT_URL` / `LLM_EMBED_URL` (LMForge on Mac).

## 2. Target state — page-level routing

```
DocIntel host (Mac, containers ~600 MB each)
├── ingestion-service  : orchestration + per-page router; no model files
├── document-service   : unchanged
├── rag-service        : pure HTTP client to LMForge
├── api-gateway        : unchanged
├── web-ui             : unchanged
└── (no infinity sidecar)
       │
       │ HTTP via host.docker.internal:11430
       ▼
LMForge (Mac, oMLX) — chat, embed, rerank, VLM all hot in unified memory
```

### 2.1 The four-path routing model

Per-page decision (extending `pdf_probe.py`):

| Path | Tech | Per-page cost | Memory | Best for |
|---|---|---|---|---|
| **A** Digital text | PyMuPDF (already bundled with docling) | 5–30 ms (CPU) | tiny | Pages with embedded text layer (most modern PDFs) |
| **B** Layout + digital | Docling layout (DETR) + text + TableFormer | 300–800 ms (CPU) | ~280 MB (already baked) | Digital PDFs with tables / multi-column / headings |
| **C** Lightweight OCR | Tesseract 5 (LSTM) | 500–1500 ms (CPU) | ~150 MB | Scanned pages with clean printed text |
| **E** VLM | `qwen2.5-vl:3b:8bit` (MLX) via LMForge | 2–4 s (Apple Silicon) | remote | Anything C can't handle: equations, complex diagrams, low-quality scans, handwriting |

(Path D — Marker — is **Proxmox-only Phase 2**. See §10.)

### 2.2 Routing decision tree

For each page (computed in `pdf_probe.py`):

```
text_density   = chars in PDF text layer / page area (cm²)
bitmap_ratio   = bitmap pixels / total pixels
has_table_hint = layout DETR detects a table region

if text_density > 0.5 and bitmap_ratio < 0.05:
    → Path A
elif text_density > 0.3 and (has_table_hint or layout_complex):
    → Path B
elif bitmap_ratio > 0.5 and text_density < 0.05:
    # Scanned page — try cheap OCR first
    result, confidence = Path C
    if confidence < 0.65 or has_math_chars(result) or has_table_hint:
        → escalate to Path E
    else:
        → keep result from C
else:
    → Path B  (mixed digital + bitmap)
```

`has_math_chars()` is a tiny regex over Tesseract output:
`[∑∫√≥≤≠π∂∇αβγθλμω]|[a-z]_\{|\^\{|\\frac` → escalate.

On a typical NCERT textbook this lands at roughly: A 35% / B 35% / C 20% / E 10%. End-to-end estimate: **2-6 minutes for a 208-page book on M3 Pro**, vs the 8+ hours that timed out.

### 2.3 Model selection per LMForge endpoint

| DocIntel call | Endpoint | Model on Mac (this doc) |
|---|---|---|
| Embeddings (ingest + query) | `/v1/embeddings` | `qwen3-embed:0.6b:8bit` |
| RAG chat | `/v1/chat/completions` | `qwen3.5:4b:4bit` (or current default) |
| Reranking | `/v1/rerank` | `bge-reranker-v2-m3:f16` (works on oMLX) |
| Bitmap-page OCR escalation | `/v1/chat/completions` multimodal | **`qwen2.5-vl:3b:8bit`** (~3 GB MLX) |
| Domain classification | (still local for now — see §7) | DeBERTa zero-shot |

VLM choice rationale: 3B at 8-bit gives the best quality/RAM trade-off for an M3 Pro that also runs IDEs / Docker / browsers. Stays under ~3.2 GB resident vs ~5 GB for 7B Q4 — leaves more unified memory for everything else. Quality on document OCR at Q8 is virtually indistinguishable from Q4 7B for our extraction prompt.

Memory budget on M3 Pro 38 GB unified:

| Component | Hot RAM |
|---|---|
| Qwen2.5-VL-3B Q8 (MLX) | ~3.2 GB |
| Qwen3.5-4B Q4 chat (MLX) | ~2.5 GB |
| Qwen3-Embed-0.6B Q8 | ~0.7 GB |
| bge-reranker-v2-m3 (whatever LMForge resolves on oMLX) | ~0.4 GB |
| **LMForge total resident** | **~7 GB** |
| Docker (DocIntel stack) | ~5 GB |
| macOS + IDEs + browsers | ~10–15 GB |
| **Total committed** | **~22 GB / 38 GB** — comfortable headroom |

## 3. Configuration changes

### 3.1 `config/defaults.env`

```diff
-LLM_CHAT_URL=http://host.docker.internal:11430/v1
-LLM_EMBED_URL=http://host.docker.internal:11430/v1
+LLM_CHAT_URL=http://host.docker.internal:11430/v1
+LLM_EMBED_URL=http://host.docker.internal:11430/v1
+LLM_VLM_URL=http://host.docker.internal:11430/v1
+LLM_RERANK_URL=http://host.docker.internal:11430/v1
+
+LLM_VLM_MODEL=qwen2.5-vl:3b:8bit
+LLM_RERANK_MODEL=bge-reranker-v2-m3:f16
```

Drop the Infinity sidecar settings:

```diff
-INFINITY_IMAGE=michaelf34/infinity:0.0.77
-INFINITY_ENGINE=optimum
+# Infinity removed — reranking served by LMForge /v1/rerank (works on oMLX/macOS)
```

`LLM_ENGINE=lmforge` stays; we're consolidating onto LMForge fully.

### 3.2 `docker-compose.yml`

Three changes:

**a)** New `LLM_VLM_*` env on `ingestion-service`:

```yaml
- LLM_VLM_URL=${LLM_VLM_URL:-http://host.docker.internal:11430/v1}
- LLM_VLM_MODEL=${LLM_VLM_MODEL:-qwen2.5-vl:3b:8bit}
- VLM_MAX_CONCURRENCY=${VLM_MAX_CONCURRENCY:-2}
- VLM_RENDER_DPI=${VLM_RENDER_DPI:-200}
```

**b)** Replace `RERANKER_URL=http://infinity:7997` on `rag-service` with:

```yaml
- LLM_RERANK_URL=${LLM_RERANK_URL:-http://host.docker.internal:11430/v1}
- LLM_RERANK_MODEL=${LLM_RERANK_MODEL:-bge-reranker-v2-m3:f16}
```

**c)** Delete the entire `infinity:` service block + any `depends_on: [infinity]` references.

Keep `extra_hosts: host.docker.internal:host-gateway`.

### 3.3 `.env.example`

Mirror §3.1.

## 4. Ingestion service — per-page routing implementation

### 4.1 Extend `pdf_probe.py` with per-page profile

File: `services/ingestion-service/src/pdf_probe.py`

Currently returns a single `PdfProfile` with document-level `strategy`. Add per-page output:

```python
@dataclass
class PageProfile:
    index: int                  # zero-based
    text_chars: int             # chars in PDF text layer
    text_density: float         # text_chars / area (cm²)
    bitmap_ratio: float         # 0..1
    width_pt: float
    height_pt: float

@dataclass
class PdfProfile:
    page_count: int
    strategy: str               # legacy doc-level summary, kept for logs
    pages: list[PageProfile]    # NEW
```

Implementation: walk pages with `pypdfium2`, count text via `page.get_textpage().get_text_range()`, compute area, sample-render a 64-pixel-wide thumbnail to estimate bitmap_ratio (cheap — keep current sampling).

### 4.2 New routing module: `services/ingestion-service/src/page_router.py`

Pure decision logic, no I/O:

```python
from enum import Enum
from .pdf_probe import PageProfile

class Path(str, Enum):
    DIGITAL = "A"
    LAYOUT  = "B"
    OCR     = "C"
    VLM     = "E"

def initial_path(p: PageProfile, has_table_hint: bool = False) -> Path:
    if p.text_density > 0.5 and p.bitmap_ratio < 0.05:
        return Path.DIGITAL
    if p.text_density > 0.3 and has_table_hint:
        return Path.LAYOUT
    if p.bitmap_ratio > 0.5 and p.text_density < 0.05:
        return Path.OCR  # may escalate to VLM after low confidence
    return Path.LAYOUT

_MATH_RE = re.compile(r"[∑∫√≥≤≠π∂∇αβγθλμω]|[a-z]_\{|\^\{|\\frac")

def should_escalate_to_vlm(ocr_text: str, ocr_confidence: float,
                           has_table_hint: bool) -> bool:
    if ocr_confidence < 0.65: return True
    if has_table_hint: return True
    if _MATH_RE.search(ocr_text or ""): return True
    return False
```

### 4.3 New extraction modules

Three thin classes, one per non-trivial path. Path A (digital) is just `pypdfium2.get_text` — inline.

#### `services/ingestion-service/src/extractors/digital.py`
```python
def extract_digital(pdf_path: Path, page_idx: int) -> str:
    doc = pdfium.PdfDocument(str(pdf_path))
    return doc[page_idx].get_textpage().get_text_range()
```

#### `services/ingestion-service/src/extractors/layout.py`
Wraps the existing Docling pipeline with `do_ocr=False`. Already mostly there in `pipeline.py` — refactor into a callable.

#### `services/ingestion-service/src/extractors/ocr_tesseract.py`
```python
import pytesseract
from PIL import Image
import pypdfium2 as pdfium

def extract_tesseract(pdf_path: Path, page_idx: int, dpi: int = 200) -> tuple[str, float]:
    """Returns (markdown_text, mean_word_confidence_0_to_1)."""
    doc = pdfium.PdfDocument(str(pdf_path))
    bitmap = doc[page_idx].render(scale=dpi/72.0)
    img = bitmap.to_pil()
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT,
                                     config="--oem 1 --psm 6")
    confidences = [int(c) for c in data["conf"] if c.isdigit() and int(c) >= 0]
    text = pytesseract.image_to_string(img, config="--oem 1 --psm 6")
    avg_conf = (sum(confidences) / len(confidences) / 100.0) if confidences else 0.0
    return text, avg_conf
```

Add to `services/ingestion-service/Dockerfile`:
```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
```

Add to `pyproject.toml`: `"pytesseract>=0.3.10"` and `"pillow>=10.0.0"` (likely already there transitively).

#### `services/ingestion-service/src/extractors/vlm.py`
Async client to LMForge `/v1/chat/completions` with multimodal content blocks.

```python
import asyncio, base64, io, logging
from pathlib import Path
import httpx
import pypdfium2 as pdfium

logger = logging.getLogger(__name__)

_PROMPT = (
    "Extract all readable content from this document page as well-formed Markdown. "
    "Preserve headings, lists, tables (as Markdown tables), code blocks, and equations. "
    "For diagrams or figures, briefly describe them in italics. "
    "Do NOT add commentary, do NOT wrap output in ``` fences. Output ONLY the page content."
)

class VlmExtractor:
    def __init__(self, base_url: str, model: str, api_key: str = "none",
                 max_concurrency: int = 2, timeout_s: float = 180.0):
        self._url     = f"{base_url.rstrip('/')}/chat/completions"
        self._model   = model
        self._headers = {"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"}
        self._sem     = asyncio.Semaphore(max_concurrency)
        self._client  = httpx.AsyncClient(timeout=timeout_s)

    async def extract(self, pdf_path: Path, page_idx: int, dpi: int = 200,
                      max_retries: int = 3) -> str:
        png_b64 = await asyncio.to_thread(self._render_to_b64, pdf_path, page_idx, dpi)
        body = {
            "model": self._model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
                ],
            }],
            "temperature": 0.0,
            "max_tokens": 4096,
            "keep_alive": "10m",   # LMForge: keep VLM hot between pages
        }
        async with self._sem:
            for attempt in range(max_retries):
                try:
                    r = await self._client.post(self._url, json=body, headers=self._headers)
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"]
                    if r.status_code == 503:    # LMForge concurrency_limit
                        retry_after = int(r.headers.get("Retry-After", "1"))
                        await asyncio.sleep(retry_after)
                        continue
                    if 500 <= r.status_code < 600:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    r.raise_for_status()
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    logger.warning("VLM transient error (attempt %d): %s", attempt+1, e)
                    await asyncio.sleep(2 ** attempt)
            raise RuntimeError(f"VLM extraction failed after {max_retries} retries")

    @staticmethod
    def _render_to_b64(pdf_path: Path, page_idx: int, dpi: int) -> str:
        doc = pdfium.PdfDocument(str(pdf_path))
        bitmap = doc[page_idx].render(scale=dpi/72.0)
        buf = io.BytesIO()
        bitmap.to_pil().save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")

    async def aclose(self):
        await self._client.aclose()
```

Honour LMForge's `503 concurrency_limit` + `Retry-After` per the README. Use `keep_alive: "10m"` to prevent the VLM from being LRU-evicted between pages of the same document.

### 4.4 Wire the router into the sharded pipeline

File: `services/ingestion-service/src/pipeline.py`, inside `_run_pdf_sharded`:

Replace the OCR-everywhere loop with:

```python
from .page_router import Path as RoutePath, initial_path, should_escalate_to_vlm
from .extractors.digital import extract_digital
from .extractors.layout  import extract_layout
from .extractors.ocr_tesseract import extract_tesseract
from .extractors.vlm import VlmExtractor

vlm = VlmExtractor(cfg.llm_vlm_url, cfg.llm_vlm_model, cfg.llm_api_key,
                   max_concurrency=cfg.vlm_max_concurrency)

for page_idx in range(start_page, end_page + 1):
    pp = profile.pages[page_idx]
    table_hint = await asyncio.to_thread(detect_table_region, pdf_path, page_idx)  # cached layout pass
    path = initial_path(pp, has_table_hint=table_hint)

    if path == RoutePath.DIGITAL:
        page_md = extract_digital(pdf_path, page_idx)
    elif path == RoutePath.LAYOUT:
        page_md = extract_layout(pdf_path, page_idx)   # docling, do_ocr=False
    elif path == RoutePath.OCR:
        text, conf = await asyncio.to_thread(extract_tesseract, pdf_path, page_idx, cfg.vlm_render_dpi)
        if should_escalate_to_vlm(text, conf, table_hint):
            logger.info("page %d: escalating to VLM (conf=%.2f, table=%s)", page_idx, conf, table_hint)
            page_md = await vlm.extract(pdf_path, page_idx, cfg.vlm_render_dpi)
        else:
            page_md = text
    else:  # VLM (rarely chosen at initial routing)
        page_md = await vlm.extract(pdf_path, page_idx, cfg.vlm_render_dpi)

    # Existing chunk + embed + persist flow — unchanged
    shard_chunks.extend(chunk_markdown(page_md, metadata={"page": page_idx + 1, "path": path.value}))

await vlm.aclose()
```

Track `path` in chunk metadata so we can analyze hit rates per document later.

### 4.5 Drop OCR + heavy deps from `ingestion-service`

`services/ingestion-service/pyproject.toml`:

```diff
-    "rapidocr-onnxruntime>=1.3.0",
+    "pytesseract>=0.3.10",
+    "pillow>=10.0.0",
```

If §7 path is taken (LLM-based domain classifier), also:

```diff
-    "transformers>=4.40.0",
-    "torch>=2.0.0",
-    "accelerate>=0.26.0",
```

### 4.6 Update `services/ingestion-service/Dockerfile`

```diff
+# Tesseract for Path C (lightweight CPU OCR)
+RUN apt-get update && apt-get install -y --no-install-recommends \
+    tesseract-ocr tesseract-ocr-eng \
+    && rm -rf /var/lib/apt/lists/*
```

Layout/TableFormer model bake stays — they're still used by Path B. Drop only the OCR model bake (we removed easyocr earlier; nothing left to drop).

### 4.7 Update `services/ingestion-service/src/config.py`

```diff
+    # --- VLM (LMForge) ---
+    llm_vlm_url: str = Field(default="http://host.docker.internal:11430/v1", alias="LLM_VLM_URL")
+    llm_vlm_model: str = Field(default="qwen2.5-vl:3b:8bit", alias="LLM_VLM_MODEL")
+    vlm_max_concurrency: int = Field(default=2, alias="VLM_MAX_CONCURRENCY")
+    vlm_render_dpi: int = Field(default=200, alias="VLM_RENDER_DPI")
+
     docling_do_ocr: bool = Field(default=False, alias="DOCLING_DO_OCR")  # was True
```

`docling_do_ocr` defaults to `false` everywhere — Docling is text/layout only now.

### 4.8 Drop the OCR config branch in `pipeline.py`

Inside `_build_pdf_pipeline_options()`, the `if options.do_ocr: options.ocr_options = RapidOcrOptions(...)` block becomes dead code. Remove the import + the block. Add a comment that OCR is handled outside Docling.

## 5. RAG service — reranker via LMForge `/v1/rerank`

File: `services/rag-service/src/components/reranker.py`

Replace the local sentence-transformers / Infinity client with a thin HTTP client:

```python
import httpx
from typing import List
from haystack import component, Document

@component
class LmforgeReranker:
    def __init__(self, base_url: str, model: str, api_key: str = "none",
                 top_k: int = 10, timeout_s: float = 30.0):
        self._url     = f"{base_url.rstrip('/')}/rerank"
        self._model   = model
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._top_k   = top_k
        self._client  = httpx.Client(timeout=timeout_s)

    @component.output_types(documents=List[Document])
    def run(self, query: str, documents: List[Document]):
        if not documents:
            return {"documents": []}
        body = {"model": self._model, "query": query,
                "documents": [d.content for d in documents],
                "top_n": self._top_k}
        r = self._client.post(self._url, json=body, headers=self._headers)
        r.raise_for_status()
        ranked = r.json()["results"]   # [{"index": int, "relevance_score": float}, ...]
        out = []
        for item in ranked:
            doc = documents[item["index"]]
            doc.score = float(item["relevance_score"])
            out.append(doc)
        return {"documents": out}
```

Update `services/rag-service/src/config.py`:

```diff
+    llm_rerank_url: str   = Field(default="http://host.docker.internal:11430/v1", alias="LLM_RERANK_URL")
+    llm_rerank_model: str = Field(default="bge-reranker-v2-m3:f16",               alias="LLM_RERANK_MODEL")
```

Drop `RERANKER_URL`, sentence-transformers, torch, onnxruntime from `pyproject.toml`.

## 6. Drop the Infinity sidecar (clean cut)

Files / sections to delete:
- `docker-compose.yml` → `infinity:` service block + `depends_on: [infinity]` references
- `services/rag-service/Dockerfile` → torch/sentence-transformers if no longer needed
- `services/rag-service/pyproject.toml` → drop `sentence-transformers`, `onnxruntime`, `torch` (verify nothing else uses them)
- `scripts/start.sh`, `scripts/setup.sh` → drop `--profile infinity` paths
- Any `INFINITY_*` env references in `config/defaults.env`

## 7. Domain classifier — keep local for now

Two options:
- **7a (keep local)**: zero churn. ~440 MB RAM penalty per service. `transformers` + `torch` stay.
- **7b (LLM-based)**: small chat call to Qwen3-1.7B with structured-output prompt. Frees ~3 GB image size and ~440 MB RAM per service.

**Decision: defer to a follow-up.** Do option 7b in a separate PR after the main migration is stable. Add a config flag `DOMAIN_CLASSIFIER=local|remote` and keep both paths until the remote one is validated.

## 8. Network configuration

LMForge runs on the Mac host. DocIntel runs in Docker Desktop. Nothing new to configure:

- `docker-compose.yml` already declares `extra_hosts: host.docker.internal:host-gateway` for `ingestion-service` and `rag-service` (verified)
- LMForge listens on `127.0.0.1:11430` by default and `trusted_networks` covers loopback — Docker Desktop's gateway IP falls under one of the RFC1918 ranges so no token needed

If anything goes wrong, fallback is to set `LMFORGE_BIND=0.0.0.0:11430` and use the host's LAN IP.

## 9. Migration sequence (zero-downtime, feature-flag driven)

1. **Bring up LMForge on Mac** with `auto_load = ["qwen3-embed:0.6b:8bit", "qwen3.5:4b:4bit", "qwen2.5-vl:3b:8bit", "bge-reranker-v2-m3:f16"]`. Verify all four endpoints with curl:
   - `/v1/embeddings`
   - `/v1/chat/completions` (text-only)
   - `/v1/chat/completions` (with `image_url`)
   - `/v1/rerank`
2. **Add new config keys to DocIntel** (§3, §4.7). DocIntel still works; new keys unused.
3. **Implement `vlm.py`** (§4.3) — unit test against the live LMForge VLM endpoint with a sample image.
4. **Implement Tesseract path** (§4.3) — unit test on a known scanned page.
5. **Implement `pdf_probe.py` per-page profile + `page_router.py`** (§4.1, §4.2) — unit tests on synthetic profiles.
6. **Wire into `pipeline.py` behind feature flag** `INGESTION_USE_PAGE_ROUTING=false` (default off). Existing tests pass.
7. **Flip flag on dev** → ingest a small mixed PDF → verify chunks + `path` metadata.
8. **Ingest the NCERT textbook** → verify completion under 10 minutes, no OOM.
9. **Switch reranker to LMForge** (§5) → run RAG smoke tests.
10. **Drop Infinity** from compose → restart stack.
11. **Drop heavy deps** from `ingestion-service` and `rag-service` images → rebuild → verify image size drop.
12. **(Phase 2 follow-up)** Replace domain classifier with LLM-based.

Each step is independently revertable.

## 10. Phase 2 — Path D (Marker) on Proxmox box

**Mac note:** Marker has known MPS issues — `TableRecEncoderDecoderModel` was forced to CPU after MPS produced wrong results from `scaled_dot_product_attention` on non-contiguous tensors (datalab-to/marker #960); layout encoder crashes on PDFs > ~66 pages on MPS (#993); `--device mps + multiprocessing` is broken (#255). v1.9.0+ is ~20× slower on Mac than v1.8. **Conclusion: Path D is NOT viable on M3 Pro.** Skip on Mac, revisit on the Proxmox RTX 5060 box where CUDA is available.

When the Proxmox box is online (after Doc 01 is done), Path D becomes a strong candidate for academic / equation-heavy / table-heavy PDFs. On CUDA, Marker is ~200 ms per page and produces structured Markdown that beats both Tesseract and a single-shot VLM call.

### 10.1 Where Path D fits in the routing tree

```
... existing tree ...
elif bitmap_ratio > 0.5 and text_density < 0.05:
    text, conf = Path C (Tesseract)
    if should_escalate_to_vlm(...):
        if marker_available and is_complex_doc(profile):  # NEW
            → Path D (Marker on Proxmox CUDA)
        else:
            → Path E (VLM)
```

`is_complex_doc()` heuristic: equations regex hit AND table_hint AND >5 such pages already seen → switch to Marker for the rest of the doc (it's more efficient batch-wise than per-page VLM calls).

### 10.2 Implementation sketch

- Run Marker as a separate FastAPI microservice in a Docker container on the Ubuntu VM (`marker-service` container with PyTorch CUDA + Marker + Surya bundled — image ~6 GB).
- Endpoint: `POST /convert` accepts a PDF (or page range) → returns Markdown.
- Add `MARKER_URL` env to `ingestion-service`.
- Add a `MarkerExtractor` class mirroring the `VlmExtractor` shape.
- Use `marker_single` for single-PDF mode (avoids the multiprocessing pitfalls; one Marker per ingestion-service worker).
- Bake Marker models (Surya layout/text/table) into the image to avoid first-run downloads.

### 10.3 Routing strategy on Proxmox

| Page type | Path |
|---|---|
| Digital text | A (PyMuPDF) |
| Digital + tables | B (Docling) |
| Scanned, clean text | C (Tesseract) |
| Scanned, messy / handwriting | E (VLM) |
| Scanned, equations or many tables | **D (Marker)** |
| Single-page complex extraction | E (VLM, faster cold start) |
| Whole-document academic PDF | **D (Marker, batch-efficient)** |

### 10.4 Acceptance criteria for Path D

- [ ] `marker-service` container runs on Ubuntu VM, GPU-accelerated
- [ ] Single-page latency < 1s on RTX 5060 for typical academic page
- [ ] Tables and equations come out as Markdown / LaTeX
- [ ] Routing tree picks Marker for documents matching the heuristic
- [ ] No regression on Mac (Path D is gated by `MARKER_URL` being set)

## 11. Files-touched checklist

| File | Change |
|---|---|
| `config/defaults.env` | URL + model defaults; drop Infinity |
| `.env.example` | Mirror the above |
| `docker-compose.yml` | URLs + new VLM env, drop `infinity` service |
| `services/ingestion-service/pyproject.toml` | Drop `rapidocr-onnxruntime`, add `pytesseract`; (later) drop `torch`, `transformers` |
| `services/ingestion-service/Dockerfile` | Add `tesseract-ocr` apt; remove model bakes that are no longer needed |
| `services/ingestion-service/src/config.py` | Add `llm_vlm_*`, `vlm_*` fields; flip `docling_do_ocr` default |
| `services/ingestion-service/src/pdf_probe.py` | Per-page `PageProfile` output |
| `services/ingestion-service/src/page_router.py` | **New** — pure routing logic |
| `services/ingestion-service/src/extractors/digital.py` | **New** |
| `services/ingestion-service/src/extractors/layout.py` | **New** — refactored from pipeline.py |
| `services/ingestion-service/src/extractors/ocr_tesseract.py` | **New** |
| `services/ingestion-service/src/extractors/vlm.py` | **New** |
| `services/ingestion-service/src/pipeline.py` | Replace OCR-everywhere loop with router; drop OCR config branch |
| `services/rag-service/pyproject.toml` | Drop `sentence-transformers`, `onnxruntime`, `torch` |
| `services/rag-service/src/config.py` | Add `llm_rerank_url`, `llm_rerank_model` |
| `services/rag-service/src/components/reranker.py` | Replace with HTTP client to `/v1/rerank` |
| `services/rag-service/Dockerfile` | Remove HF model bake / torch deps if dropped |
| `scripts/start.sh`, `scripts/setup.sh` | Drop Infinity profile + paths |

## 12. Testing plan

### 12.1 Unit
- `pytest services/ingestion-service/tests/test_page_router.py` — synthetic PageProfile inputs
- `pytest services/ingestion-service/tests/test_extractors.py` — mocked LMForge for VLM, real Tesseract on a tiny fixture

### 12.2 Integration
- Compose stack up
- Upload 5-page mixed PDF (digital + scan) — verify each path is exercised at least once via `path` metadata
- Upload `ncert_grade7_science.pdf` — verify completion < 10 min, peak ingestion-service RAM < 1 GB
- RAG query against new chunks — verify reranker via LMForge

### 12.3 Failure modes
- Stop LMForge → ingestion-service fails cleanly with `vlm_endpoint_unreachable` (don't crash); existing DLQ flow handles retry
- Restart LMForge → next ingestion attempt succeeds
- Force LMForge to return 503 (concurrency_limit) → verify `Retry-After` honored

## 13. Acceptance criteria

- [ ] All four LMForge endpoints called by DocIntel during a single end-to-end user flow
- [ ] `infinity` service removed from `docker compose ps`
- [ ] `ingestion-service` peak RAM during 208-page PDF < 1 GB
- [ ] 208-page NCERT textbook ingests in < 10 minutes on M3 Pro
- [ ] `pip list` inside `ingestion-service` does not contain `easyocr`, `rapidocr-onnxruntime`
- [ ] `pip list` inside `rag-service` does not contain `sentence-transformers`, `onnxruntime`
- [ ] Per-chunk metadata includes `path` field showing which extractor was used
- [ ] Stopping LMForge gives clean errors, not crashes
- [ ] All unit + integration tests green

---

Done. Three docs in `tasks/`:

1. `01-proxmox-hardware-setup.md` — Phase 2: Proxmox host, GPU passthrough, VMs, LXCs
2. `02-lmforge-changes.md` — historical reference; mostly superseded by upstream LMForge updates
3. `03-docintel-changes.md` — this file: per-page routing on Mac (now), Marker added on Proxmox (Phase 2)

Read order for implementation: **Doc 03 first** (Mac, all four paths A/B/C/E), then **Doc 01 + 02** later when the Proxmox box arrives, then add **Path D** per §10.
