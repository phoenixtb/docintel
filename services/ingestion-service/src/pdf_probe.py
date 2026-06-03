"""
PDF probe — cheap pre-flight scan to decide OCR strategy before Docling runs.

Samples the first, middle, and last page of a PDF via pypdfium2 (the same backend
docling uses internally). Measures:
  - average text-cell character count (high → digital PDF with embedded text)
  - average bitmap coverage (high → scanned / image-heavy PDF)

Returns a PdfProfile (document-level) and can return per-page PageProfile list
(for the per-page router in Phase 2/3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PdfProfile:
    strategy: str          # "digital" | "hybrid" | "scanned"
    do_ocr: bool
    force_full_page_ocr: bool
    force_backend_text: bool
    page_count: int = 0
    avg_text_chars: float = 0.0
    avg_bitmap_cov: float = 0.0
    extra: dict = field(default_factory=dict)


def probe_pdf(path: Path) -> PdfProfile:
    """
    Probe a PDF file and return a routing profile.

    Uses pypdfium2 (bundled with docling) so there is no additional dependency.
    Falls back to the "hybrid" (safe) profile if probing fails.

    Thresholds (tunable via env later):
      avg_text_chars > 500 AND avg_bitmap < 0.30  → digital  (text extraction only)
      avg_text_chars > 200                         → hybrid   (selective per-page OCR)
      otherwise                                    → scanned  (full-page OCR)
    """
    try:
        import pypdfium2 as pdfium  # bundled with docling
    except ImportError:
        logger.warning("pypdfium2 not available; defaulting to hybrid PDF profile")
        return PdfProfile(strategy="hybrid", do_ocr=True, force_full_page_ocr=False, force_backend_text=False)

    try:
        doc = pdfium.PdfDocument(str(path))
        total_pages = len(doc)

        if total_pages == 0:
            doc.close()
            return PdfProfile(strategy="hybrid", do_ocr=True, force_full_page_ocr=False,
                              force_backend_text=False, page_count=0)

        sample_idxs = sorted({0, total_pages // 2, total_pages - 1})

        total_text_chars = 0
        total_bitmap_cov = 0.0

        for idx in sample_idxs:
            page = doc[idx]

            # Text cell character count (embedded text in PDF)
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            total_text_chars += len(text or "")
            textpage.close()

            # Bitmap coverage: render at low resolution and measure non-white pixel ratio
            bitmap = page.render(scale=0.5)  # 0.5 = low-res for speed
            pil_img = bitmap.to_pil()
            total_bitmap_cov += _bitmap_coverage(pil_img)
            bitmap.close()
            page.close()

        doc.close()

        n = len(sample_idxs)
        avg_text = total_text_chars / n
        avg_bitmap = total_bitmap_cov / n

        logger.info(
            "PDF probe: pages=%d samples=%d avg_text_chars=%.0f avg_bitmap_cov=%.2f",
            total_pages, n, avg_text, avg_bitmap,
        )

        if avg_text > 500 and avg_bitmap < 0.30:
            strategy = "digital"
            do_ocr = False
            force_full_page_ocr = False
            force_backend_text = True
        elif avg_text > 200:
            strategy = "hybrid"
            do_ocr = True
            force_full_page_ocr = False
            force_backend_text = False
        else:
            strategy = "scanned"
            do_ocr = True
            force_full_page_ocr = True
            force_backend_text = False

        return PdfProfile(
            strategy=strategy,
            do_ocr=do_ocr,
            force_full_page_ocr=force_full_page_ocr,
            force_backend_text=force_backend_text,
            page_count=total_pages,
            avg_text_chars=avg_text,
            avg_bitmap_cov=avg_bitmap,
        )

    except Exception as exc:
        logger.warning("PDF probe failed for %s: %s — defaulting to hybrid", path, exc)
        return PdfProfile(strategy="hybrid", do_ocr=True, force_full_page_ocr=False, force_backend_text=False)


def probe_pages(path: Path, page_range: tuple[int, int] | None = None) -> list:
    """
    Return a PageProfile for each page in the given range (0-based, inclusive).

    If page_range is None, probe all pages. Imported lazily to avoid circular import
    with page_router (which must stay import-free from pdf_probe at module level).

    Falls back to an empty list on any error — callers must handle this gracefully.
    """
    from .page_router import PageProfile

    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []

    try:
        doc = pdfium.PdfDocument(str(path))
        total = len(doc)
        start, end = (page_range if page_range else (0, total - 1))
        end = min(end, total - 1)

        profiles: list[PageProfile] = []
        for idx in range(start, end + 1):
            page = doc[idx]

            # Text density
            textpage = page.get_textpage()
            text = textpage.get_text_range() or ""
            text_chars = len(text)
            textpage.close()

            # Approximate page area in points² → normalise text chars
            width = page.get_width()
            height = page.get_height()
            page_area = max(width * height, 1.0)
            # chars per 1000 pt² → clamp to [0,1]
            text_density = min(text_chars / (page_area / 1000.0) / 10.0, 1.0)

            # Bitmap coverage
            bitmap = page.render(scale=0.25)
            pil_img = bitmap.to_pil()
            bm_ratio = _bitmap_coverage(pil_img)
            bitmap.close()
            page.close()

            # Simple table heuristic: look for | or tab-heavy text, or grid-like patterns
            has_table_hint = (
                text.count("|") > 5
                or text.count("\t") > 10
                or (text_chars > 50 and text.count("\n") > 5 and text.count("  ") > 10)
            )

            # Math heuristic: common LaTeX / equation symbols
            math_symbols = {"=", "∑", "∫", "√", "π", "∆", "α", "β", "γ", "≤", "≥", "∈"}
            has_math_hint = sum(1 for s in math_symbols if s in text) >= 3

            profiles.append(PageProfile(
                page_index=idx,
                text_chars=text_chars,
                text_density=text_density,
                bitmap_ratio=bm_ratio,
                has_table_hint=has_table_hint,
                has_math_hint=has_math_hint,
            ))

        doc.close()
        return profiles

    except Exception as exc:
        logger.warning("probe_pages failed for %s: %s", path, exc)
        return []


def _bitmap_coverage(pil_img) -> float:
    """
    Fraction of pixels that are not near-white.

    A mostly-white page (digital PDF) scores near 0; a scanned page with
    ink/images scores higher. Uses a simple luminance threshold.
    """
    try:
        import numpy as np
        arr = np.array(pil_img.convert("L"))  # grayscale
        non_white = np.sum(arr < 240)  # below 240/255 = not near-white
        return float(non_white) / max(arr.size, 1)
    except Exception:
        return 0.5  # conservative default
