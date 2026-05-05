"""
Path E: VLM-based OCR via LMForge /v1/chat/completions (multimodal).

Used when Tesseract confidence is too low, or the page contains tables / math.
Renders the page to PNG, base64-encodes it, and sends to LMForge with an
extraction prompt. Respects LMForge 503 + Retry-After for back-pressure.

Concurrency is bounded by an asyncio.Semaphore sized to VLM_MAX_CONCURRENCY.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = (
    "You are a document digitization assistant. "
    "Extract ALL text from this page exactly as it appears, preserving structure. "
    "For tables: output each row on its own line with cells separated by ' | '. "
    "For equations: use LaTeX notation inline, e.g. $E = mc^2$. "
    "Output ONLY the extracted content — no preamble, no commentary."
)


# LMForge multipart body limit empirically tested at ~1.5MB.
# Stay safely under it after base64 (~33% overhead) and JSON wrapping.
_MAX_PNG_BYTES = 900_000  # 900 KB PNG → ~1.2 MB after base64+JSON


def _render_page_png(
    path: Path, page_index: int, dpi: int = 150, max_bytes: int = _MAX_PNG_BYTES
) -> bytes | None:
    """
    Render a PDF page to PNG bytes via pypdfium2.

    Progressively scales the image down if the encoded PNG exceeds ``max_bytes``
    (LMForge's request body limit). Each fallback step halves the linear DPI.
    Returns the smallest acceptable rendering, or None on render failure.
    """
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        if page_index >= len(doc):
            doc.close()
            return None
        page = doc[page_index]

        current_dpi = dpi
        last_bytes: bytes | None = None
        for attempt in range(4):
            bitmap = page.render(scale=current_dpi / 72.0)
            pil_img = bitmap.to_pil()
            bitmap.close()

            buf = io.BytesIO()
            pil_img.save(buf, format="PNG", optimize=True)
            last_bytes = buf.getvalue()

            if len(last_bytes) <= max_bytes:
                if attempt > 0:
                    logger.info(
                        "VLM render: downscaled to %d DPI (%dKB) to fit %dKB budget",
                        current_dpi, len(last_bytes) // 1024, max_bytes // 1024,
                    )
                page.close()
                doc.close()
                return last_bytes

            current_dpi = max(60, current_dpi // 2)
            if attempt == 3:
                # Last resort: keep last rendering even if oversize — let LMForge reject it cleanly.
                logger.warning(
                    "VLM render: page %d still %dKB at %d DPI after downscaling",
                    page_index, len(last_bytes) // 1024, current_dpi,
                )

        page.close()
        doc.close()
        return last_bytes
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
) -> str:
    """
    Extract text from a PDF page using a VLM via LMForge.

    Returns extracted text string. Returns empty string on unrecoverable failure.
    """
    png_bytes = await asyncio.get_event_loop().run_in_executor(
        None, _render_page_png, path, page_index, dpi
    )
    if not png_bytes:
        return ""

    b64_image = base64.b64encode(png_bytes).decode("utf-8")
    data_url = f"data:image/png;base64,{b64_image}"

    payload = {
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
        "max_tokens": 4096,
        "temperature": 0.0,
    }

    base_url = vlm_url.rstrip("/")
    endpoint = f"{base_url}/chat/completions"

    for attempt in range(max_retries):
        async with semaphore:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(endpoint, json=payload)

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
            except Exception as exc:
                logger.error(
                    "VLM extraction failed for page %d attempt %d/%d: %s",
                    page_index, attempt + 1, max_retries, exc,
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

    logger.error("VLM extraction exhausted retries for page %d of %s", page_index, path)
    return ""
