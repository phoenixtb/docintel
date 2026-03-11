"""Source adapter ABC — all adapters materialise their source as local temp files."""

from abc import ABC, abstractmethod
from pathlib import Path


class SourceAdapter(ABC):
    """
    Base class for all document source adapters.

    Each adapter fetches documents from its source (MinIO, HuggingFace, raw text)
    and materialises them as local temp files that DoclingConverter can process.
    The Haystack pipeline is source-agnostic: it only sees file paths.
    """

    @abstractmethod
    async def fetch(self, source_ref: dict) -> list[Path]:
        """
        Materialise the source to local temporary files.

        Args:
            source_ref: Source-specific reference (bucket+path, dataset key, raw text, etc.)

        Returns:
            List of local file paths for DoclingConverter to process.
            The caller is responsible for cleaning up the temp directory.
        """
        ...

    @abstractmethod
    def source_type(self) -> str:
        """Return a short identifier for the source type (e.g. 'minio', 'hf', 'text')."""
        ...
