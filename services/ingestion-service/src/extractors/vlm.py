"""
Path E: VLM-based OCR via LMForge /v1/chat/completions (multimodal).

Used when Tesseract confidence is too low, or the page contains tables / math.
Renders the page to PNG at the configured DPI, base64-encodes it, and sends to
LMForge with an extraction prompt. Respects LMForge 503 + Retry-After for
back-pressure. Treats 413 (Payload Too Large) as terminal — the caller's
LMForge body limit must be raised, not silently downscaled (that would degrade
extraction quality without the operator noticing).

Sampling parameters (temperature, top_p, frequency_penalty, repetition_penalty,
…) are resolved by the caller via ModelProfileResolver and passed in. This is
how the user breaks VLM repetition loops on dense text pages without changing
the model itself: tune the profile for `qwen2.5-vl:*` in the UI, redeploy the
config (cache invalidates after TTL), repetition stops.

Concurrency is bounded by an asyncio.Semaphore sized to VLM_MAX_CONCURRENCY.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


@dataclass
class VlmSamplingParams:
    """
    Sampling params actually sent to LMForge for a VLM call.

    All fields optional — None means "omit from payload, let the engine use
    its own default". Callers (pipeline._run_pdf_sharded) populate these from
    ModelProfileResolver so DB profiles + builtin defaults flow through.
    """
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    repetition_penalty: float | None = None
    top_k: int | None = None
    min_p: float | None = None

_EXTRACTION_PROMPT = (
    "You are a document digitization assistant. "
    "Extract ALL text from this page exactly as it appears, preserving structure. "
    "For tables: output each row on its own line with cells separated by ' | '. "
    "For equations: use LaTeX notation inline, e.g. $E = mc^2$. "
    "Output ONLY the extracted content — no preamble, no commentary."
)


def _render_page_png(path: Path, page_index: int, dpi: int = 150) -> bytes | None:
    """Render a PDF page to PNG bytes via pypdfium2 at the requested DPI."""
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        if page_index >= len(doc):
            doc.close()
            return None
        page = doc[page_index]
        bitmap = page.render(scale=dpi / 72.0)
        pil_img = bitmap.to_pil()
        bitmap.close()
        page.close()
        doc.close()

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("page render failed for page %d of %s: %s", page_index, path, exc)
        return None


async def extract_vlm(
    path: Path,
    page_index: int,
    vlm_url: str,
    vlm_model: str,
    semaphore: asyncio.Semaphore,
    dpi: int = 150,
    timeout: float = 120.0,
    max_retries: int = 3,
    sampling: VlmSamplingParams | None = None,
) -> str:
    """
    Extract text from a PDF page using a VLM via LMForge.

    Returns extracted text string. Returns empty string on unrecoverable failure.

    Sampling params come from ModelProfileResolver via the `sampling` arg.
    None values are omitted from the JSON payload so LMForge / the engine
    keeps its own defaults — never silently overrides what the operator chose.

    Failure modes:
      - 413 Payload Too Large: terminal. The PNG body exceeds LMForge's
        request-size limit. Raise LMForge's body limit; do not retry.
      - 503 Service Unavailable: respects Retry-After header (model loading).
      - Timeout / 5xx / network: exponential backoff up to max_retries.
    """
    png_bytes = await asyncio.get_event_loop().run_in_executor(
        None, _render_page_png, path, page_index, dpi
    )
    if not png_bytes:
        return ""

    b64_image = base64.b64encode(png_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{b64_image}"

    sp = sampling or VlmSamplingParams()
    payload: dict = {
        "model": vlm_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _EXTRACTION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "keep_alive": "10m",
    }
    # Map resolved params → OpenAI-compatible keys. Penalties beyond OpenAI's
    # spec (repetition_penalty / top_k / min_p) are forwarded as-is — LMForge
    # accepts them and routes to MLX. Engines that don't recognise them ignore
    # the field, so this stays portable.
    _maybe_set = lambda k, v: payload.update({k: v}) if v is not None else None  # noqa: E731
    _maybe_set("temperature", sp.temperature if sp.temperature is not None else 0.1)
    _maybe_set("top_p", sp.top_p)
    _maybe_set("max_tokens", sp.max_tokens if sp.max_tokens is not None else 4096)
    _maybe_set("frequency_penalty", sp.frequency_penalty)
    _maybe_set("presence_penalty", sp.presence_penalty)
    _maybe_set("repetition_penalty", sp.repetition_penalty)
    _maybe_set("top_k", sp.top_k)
    _maybe_set("min_p", sp.min_p)

    base_url = vlm_url.rstrip("/")
    endpoint = f"{base_url}/chat/completions"

    png_kb = len(png_bytes) // 1024
    body_kb = len(b64_image) // 1024  # base64 dominates body size

    for attempt in range(max_retries):
        async with semaphore:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(endpoint, json=payload)

                    # 413 is terminal — same payload will fail every time. Operator must
                    # raise LMForge's body limit; we deliberately do NOT downscale here
                    # because silent quality degradation is worse than a loud failure.
                    if resp.status_code == 413:
                        logger.error(
                            "VLM 413 Payload Too Large for page %d (PNG %dKB, base64 %dKB at %d DPI). "
                            "Raise LMForge's request-body limit — DO NOT lower DPI.",
                            page_index, png_kb, body_kb, dpi,
                        )
                        return ""

                    if resp.status_code == 503:
                        retry_after = int(resp.headers.get("Retry-After", "5"))
                        logger.warning(
                            "LMForge 503 (model loading), retrying in %ds (attempt %d/%d)",
                            retry_after, attempt + 1, max_retries,
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"]
                    return content.strip()

            except httpx.TimeoutException:
                logger.warning(
                    "VLM timeout on page %d attempt %d/%d", page_index, attempt + 1, max_retries
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except httpx.HTTPStatusError as exc:
                # 4xx other than 413 → terminal (auth, bad request, etc.). 5xx → retry.
                status = exc.response.status_code
                if 400 <= status < 500:
                    logger.error(
                        "VLM extraction failed for page %d: %d %s — terminal, not retrying",
                        page_index, status, exc.response.reason_phrase,
                    )
                    return ""
                logger.error(
                    "VLM extraction failed for page %d attempt %d/%d: %d %s",
                    page_index, attempt + 1, max_retries, status, exc.response.reason_phrase,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except Exception as exc:
                logger.error(
                    "VLM extraction failed for page %d attempt %d/%d: %s",
                    page_index, attempt + 1, max_retries, exc,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

    logger.error("VLM extraction exhausted retries for page %d of %s", page_index, path)
    return ""
