"""
Chunking Service
================

Production chunking using Chonkie library.
Supports multiple strategies with consistent interface.
"""

from pydantic import BaseModel
from enum import Enum
from typing import Optional
import os


class ChunkingMethod(str, Enum):
    RECURSIVE = "recursive"  # Best balance of quality and speed
    SEMANTIC = "semantic"  # Embedding-based topic boundaries
    TOKEN = "token"  # Fixed token count


class ChunkingConfig(BaseModel):
    method: ChunkingMethod = ChunkingMethod.RECURSIVE
    chunk_size: int = 400  # tokens
    chunk_overlap: int = 0


class ChunkResult(BaseModel):
    content: str
    start_char: int
    end_char: int
    token_count: int
    metadata: dict = {}


class ChunkingService:
    """
    Production chunking using Chonkie library.
    Supports multiple strategies with consistent interface.
    """

    def __init__(
        self,
        embedding_model: str = "nomic-embed-text",
        ollama_base_url: Optional[str] = None,
    ):
        self.embedding_model = embedding_model
        self.ollama_base_url = ollama_base_url or os.getenv(
            "OLLAMA_BASE_URL", "http://localhost:11434"
        )
        self._chunkers: dict = {}
        self._initialized = False

    def _initialize(self):
        """Lazy initialization of chunkers."""
        if self._initialized:
            return

        from chonkie import RecursiveChunker, TokenChunker

        # Pre-configured chunkers (Chonkie uses chunk_size only, no overlap param)
        self._chunkers = {
            ChunkingMethod.RECURSIVE: RecursiveChunker(
                chunk_size=400,
            ),
            ChunkingMethod.TOKEN: TokenChunker(
                chunk_size=400,
            ),
        }

        # Semantic chunker requires embedding model - initialize separately if needed
        self._initialized = True

    def _get_semantic_chunker(self):
        """Get or create semantic chunker with embeddings."""
        if ChunkingMethod.SEMANTIC not in self._chunkers:
            from chonkie import SemanticChunker

            # Try to use Ollama embeddings
            try:
                from chonkie.embeddings import OllamaEmbeddings

                embeddings = OllamaEmbeddings(
                    model=self.embedding_model,
                    api_url=self.ollama_base_url,
                )
            except ImportError:
                # Fallback to sentence-transformers
                from chonkie.embeddings import SentenceTransformerEmbeddings

                embeddings = SentenceTransformerEmbeddings(
                    model="nomic-ai/nomic-embed-text-v1.5",
                    trust_remote_code=True,
                )

            self._chunkers[ChunkingMethod.SEMANTIC] = SemanticChunker(
                embedding_model=embeddings,
                chunk_size=400,
                threshold=0.5,
            )

        return self._chunkers[ChunkingMethod.SEMANTIC]

    def _apply_overlap(
        self, chunks: list[ChunkResult], text: str, overlap_chars: int
    ) -> list[ChunkResult]:
        """
        Post-process chunks to add sliding-window overlap.

        Each chunk (except the first) is extended backwards to include the last
        `overlap_chars` characters from the previous chunk, preserving context
        across boundaries.
        """
        if overlap_chars <= 0 or len(chunks) <= 1:
            return chunks

        overlapped: list[ChunkResult] = []
        for i, chunk in enumerate(chunks):
            if i == 0:
                overlapped.append(chunk)
                continue

            prev_end = chunks[i - 1].end_char
            new_start = max(chunks[i - 1].start_char, prev_end - overlap_chars)
            overlapped.append(
                ChunkResult(
                    content=text[new_start : chunk.end_char],
                    start_char=new_start,
                    end_char=chunk.end_char,
                    token_count=chunk.token_count,  # approximation (no re-tokenise)
                    metadata=chunk.metadata,
                )
            )

        return overlapped

    def chunk(self, text: str, config: Optional[ChunkingConfig] = None) -> list[ChunkResult]:
        """
        Chunk text using specified method.

        Args:
            text: Text to chunk
            config: Chunking configuration (defaults to recursive, 400 tokens, 0 overlap)

        Returns:
            List of ChunkResult objects
        """
        self._initialize()
        config = config or ChunkingConfig()

        if config.method == ChunkingMethod.SEMANTIC:
            chunker = self._get_semantic_chunker()
        else:
            chunker = self._chunkers.get(
                config.method, self._chunkers[ChunkingMethod.RECURSIVE]
            )

        # Chonkie returns Chunk objects with text, start_index, end_index, token_count
        raw_chunks = chunker.chunk(text)

        chunks = [
            ChunkResult(
                content=chunk.text,
                start_char=chunk.start_index,
                end_char=chunk.end_index,
                token_count=chunk.token_count,
            )
            for chunk in raw_chunks
        ]

        # Apply overlap post-processing (≈4 chars per token)
        if config.chunk_overlap > 0:
            overlap_chars = config.chunk_overlap * 4
            chunks = self._apply_overlap(chunks, text, overlap_chars)

        return chunks

    def chunk_document(
        self,
        text: str,
        document_id: str,
        tenant_id: str,
        filename: str,
        config: Optional[ChunkingConfig] = None,
        extra_metadata: Optional[dict] = None,
    ) -> list[ChunkResult]:
        """
        Chunk a document with full metadata.

        Args:
            text: Document text
            document_id: Document ID
            tenant_id: Tenant ID for isolation
            filename: Original filename
            config: Chunking configuration
            extra_metadata: Additional metadata to include

        Returns:
            List of ChunkResult with document metadata
        """
        chunks = self.chunk(text, config)

        for i, chunk in enumerate(chunks):
            chunk.metadata = {
                "document_id": document_id,
                "tenant_id": tenant_id,
                "filename": filename,
                "chunk_index": i,
                **(extra_metadata or {}),
            }

        return chunks


# Global instance for convenience
_chunking_service: Optional[ChunkingService] = None


def get_chunking_service() -> ChunkingService:
    """Get or create global chunking service instance."""
    global _chunking_service
    if _chunking_service is None:
        _chunking_service = ChunkingService()
    return _chunking_service
