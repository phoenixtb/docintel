"""
Path A: Digital text extraction via pypdfium2.

Used for pages with high embedded-text density and low bitmap coverage.
No model loading — pure text layer extraction from the PDF itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_digital(path: Path, page_index: int) -> str:
    """
    Extract embedded text from a single PDF page (0-based index).

    Returns the raw text string. Empty string if extraction fails or page has no text.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("pypdfium2 not available for digital extraction")
        return ""

    try:
        doc = pdfium.PdfDocument(str(path))
        if page_index >= len(doc):
            doc.close()
            return ""
        page = doc[page_index]
        textpage = page.get_textpage()
        text = textpage.get_text_range() or ""
        textpage.close()
        page.close()
        doc.close()
        return text.strip()
    except Exception as exc:
        logger.warning("digital extraction failed for page %d of %s: %s", page_index, path, exc)
        return ""
