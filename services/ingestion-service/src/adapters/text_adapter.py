"""Raw text adapter — writes plain text strings as .txt temp files."""

import asyncio
import logging
import tempfile
from pathlib import Path

from .base import SourceAdapter

logger = logging.getLogger(__name__)


class TextAdapter(SourceAdapter):
    """
    Accepts raw text content and writes it to a temp .txt file for DoclingConverter.

    source_ref keys:
        content  (str): Raw text content
        filename (str, optional): Logical filename (used as temp file stem)
    """

    def source_type(self) -> str:
        return "text"

    async def fetch(self, source_ref: dict) -> list[Path]:
        content: str = source_ref["content"]
        filename: str = source_ref.get("filename", "document")

        stem = Path(filename).stem or "document"
        tmp_dir = Path(tempfile.mkdtemp(prefix="ingest_text_"))
        file_path = tmp_dir / f"{stem}.txt"
        file_path.write_text(content, encoding="utf-8")

        logger.debug("TextAdapter: wrote %d chars to %s", len(content), file_path)
        return [file_path]
