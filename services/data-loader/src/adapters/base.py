"""Source adapter ABC — all adapters yield LoadedFile objects."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator


@dataclass
class LoadedFile:
    """A single file loaded from a source, ready to be uploaded to MinIO."""
    content: bytes
    filename: str
    metadata: dict = field(default_factory=dict)


class SourceAdapter(ABC):
    """
    Base class for all document source adapters.

    Each adapter fetches documents from its source and yields LoadedFile objects.
    The caller is responsible for hashing content, uploading to MinIO, and
    registering with document-service.
    """

    @abstractmethod
    def fetch(self, config: dict, tenant_id: str) -> Iterator[LoadedFile]:
        """
        Yield LoadedFile objects for each document from the source.

        Args:
            config: Source-specific configuration (dataset_key, samples, etc.)
            tenant_id: Tenant context (used for metadata, NOT for isolation — that's
                       enforced by content hash scoping in document-service).

        Yields:
            LoadedFile instances ready for MinIO upload.
        """
        ...

    @abstractmethod
    def source_type(self) -> str:
        """Return a short identifier for the source type (e.g. 'huggingface', 's3')."""
        ...
