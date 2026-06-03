"""
Path B: Docling layout-aware extraction (do_ocr=False).

Used for pages with digital text that also contain tables / complex layout.
TableFormer runs; OCR does NOT run — text comes from the PDF layer.
Returns extracted text as a plain string for chunk assembly.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_layout(path: Path, page_index: int, artifacts_path: str = "/app/docling-cache") -> str:
    """
    Run Docling layout + TableFormer on a single page (0-based index), no OCR.

    Uses the same Docling converter config as the main pipeline but scoped to one page.
    Returns extracted text. Falls back to empty string on failure.
    """
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.datamodel.base_models import InputFormat
    except ImportError:
        logger.warning("docling not available for layout extraction")
        return ""

    try:
        opts = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=True,
            artifacts_path=artifacts_path,
        )
        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        # page_range is 1-based inclusive
        result = converter.convert(path, page_range=(page_index + 1, page_index + 1))
        text = result.document.export_to_text() if result and result.document else ""
        return text.strip()
    except Exception as exc:
        logger.warning("layout extraction failed for page %d of %s: %s", page_index, path, exc)
        return ""
