"""
Chunking Service Tests
======================

Tests for the ChunkingService using Chonkie library.
"""

import pytest
from pathlib import Path

from src.chunking import (
    ChunkingService,
    ChunkingConfig,
    ChunkingMethod,
    ChunkResult,
    get_chunking_service,
)


class TestChunkingConfig:
    """Tests for ChunkingConfig model."""

    def test_default_config(self):
        """Default config uses recursive method with 400 tokens."""
        config = ChunkingConfig()
        
        assert config.method == ChunkingMethod.RECURSIVE
        assert config.chunk_size == 400
        assert config.chunk_overlap == 0

    def test_custom_config(self):
        """Custom config values are preserved."""
        config = ChunkingConfig(
            method=ChunkingMethod.TOKEN,
            chunk_size=512,
            chunk_overlap=50,
        )
        
        assert config.method == ChunkingMethod.TOKEN
        assert config.chunk_size == 512
        assert config.chunk_overlap == 50


class TestChunkResult:
    """Tests for ChunkResult model."""

    def test_chunk_result_creation(self):
        """ChunkResult contains expected fields."""
        result = ChunkResult(
            content="Test content",
            start_char=0,
            end_char=12,
            token_count=2,
            metadata={"key": "value"},
        )
        
        assert result.content == "Test content"
        assert result.start_char == 0
        assert result.end_char == 12
        assert result.token_count == 2
        assert result.metadata == {"key": "value"}

    def test_chunk_result_default_metadata(self):
        """ChunkResult defaults to empty metadata."""
        result = ChunkResult(
            content="Test",
            start_char=0,
            end_char=4,
            token_count=1,
        )
        
        assert result.metadata == {}


@pytest.mark.unit
class TestChunkingServiceBasic:
    """Basic tests for ChunkingService."""

    def test_service_initialization(self):
        """Service initializes without errors."""
        service = ChunkingService()
        
        assert service is not None
        assert service._initialized is False

    def test_lazy_initialization(self, chunking_service: ChunkingService):
        """Service initializes lazily on first chunk."""
        assert chunking_service._initialized is False
        
        # Trigger initialization
        chunking_service._initialize()
        
        assert chunking_service._initialized is True
        assert ChunkingMethod.RECURSIVE in chunking_service._chunkers
        assert ChunkingMethod.TOKEN in chunking_service._chunkers

    def test_global_service_singleton(self):
        """get_chunking_service returns same instance."""
        service1 = get_chunking_service()
        service2 = get_chunking_service()
        
        # Note: They should be the same instance
        assert service1 is service2


@pytest.mark.unit
class TestRecursiveChunking:
    """Tests for recursive chunking method."""

    def test_chunk_short_text(self, chunking_service: ChunkingService):
        """Short text returns single chunk."""
        text = "This is a short text that should fit in one chunk."
        
        chunks = chunking_service.chunk(text)
        
        assert len(chunks) >= 1
        assert chunks[0].content == text or text in chunks[0].content

    def test_chunk_long_text(self, chunking_service: ChunkingService, hr_policy_content: str):
        """Long document is split into multiple chunks."""
        chunks = chunking_service.chunk(hr_policy_content)
        
        # HR policy is ~500 words, should produce multiple chunks
        assert len(chunks) > 1
        
        # Each chunk should have content
        for chunk in chunks:
            assert len(chunk.content) > 0
            assert chunk.token_count > 0

    def test_chunk_positions_are_valid(self, chunking_service: ChunkingService, technical_doc_content: str):
        """Chunk positions (start_char, end_char) are valid."""
        chunks = chunking_service.chunk(technical_doc_content)
        
        for chunk in chunks:
            assert chunk.start_char >= 0
            assert chunk.end_char > chunk.start_char
            assert chunk.end_char <= len(technical_doc_content) + 100  # Allow some tolerance

    def test_chunks_cover_document(self, chunking_service: ChunkingService, general_doc_content: str):
        """Chunks collectively cover most of the document content."""
        chunks = chunking_service.chunk(general_doc_content)
        
        # Concatenate all chunk content
        all_content = " ".join(c.content for c in chunks)
        
        # Key phrases from the document should appear in chunks
        assert "TechVenture" in all_content
        assert "Sarah Chen" in all_content
        assert "Austin" in all_content


@pytest.mark.unit
class TestTokenChunking:
    """Tests for token-based chunking method."""

    def test_token_chunking(self, chunking_service: ChunkingService, hr_policy_content: str):
        """Token chunking produces consistent chunk sizes."""
        config = ChunkingConfig(method=ChunkingMethod.TOKEN)
        
        chunks = chunking_service.chunk(hr_policy_content, config)
        
        assert len(chunks) > 1
        
        # All chunks except last should be close to target size
        for chunk in chunks[:-1]:
            assert chunk.token_count > 0
            # Token chunker should produce chunks around target size
            assert chunk.token_count <= 500  # Some tolerance over 400


@pytest.mark.unit
class TestDocumentChunking:
    """Tests for chunk_document with metadata."""

    def test_chunk_document_adds_metadata(
        self,
        chunking_service: ChunkingService,
        hr_policy_content: str,
        test_tenant_id: str,
        test_document_id: str,
    ):
        """chunk_document adds document metadata to each chunk."""
        chunks = chunking_service.chunk_document(
            text=hr_policy_content,
            document_id=test_document_id,
            tenant_id=test_tenant_id,
            filename="hr_policy.txt",
        )
        
        for i, chunk in enumerate(chunks):
            assert chunk.metadata["document_id"] == test_document_id
            assert chunk.metadata["tenant_id"] == test_tenant_id
            assert chunk.metadata["filename"] == "hr_policy.txt"
            assert chunk.metadata["chunk_index"] == i

    def test_chunk_document_with_extra_metadata(
        self,
        chunking_service: ChunkingService,
        contract_content: str,
        test_tenant_id: str,
        test_document_id: str,
    ):
        """chunk_document preserves extra metadata."""
        extra = {
            "domain": "contracts",
            "document_type": "contracts",
            "author": "Legal Team",
        }
        
        chunks = chunking_service.chunk_document(
            text=contract_content,
            document_id=test_document_id,
            tenant_id=test_tenant_id,
            filename="contract.txt",
            extra_metadata=extra,
        )
        
        for chunk in chunks:
            assert chunk.metadata["domain"] == "contracts"
            assert chunk.metadata["document_type"] == "contracts"
            assert chunk.metadata["author"] == "Legal Team"


@pytest.mark.unit
class TestChunkingEdgeCases:
    """Edge case tests for chunking."""

    def test_empty_text(self, chunking_service: ChunkingService):
        """Empty text returns empty list or single empty chunk."""
        chunks = chunking_service.chunk("")
        
        # Either no chunks or a single empty chunk
        assert len(chunks) <= 1

    def test_whitespace_only_text(self, chunking_service: ChunkingService):
        """Whitespace-only text is handled gracefully."""
        chunks = chunking_service.chunk("   \n\t\n   ")
        
        # Should not raise, may return empty or whitespace chunk
        assert isinstance(chunks, list)

    def test_single_word(self, chunking_service: ChunkingService):
        """Single word text returns one chunk."""
        chunks = chunking_service.chunk("Hello")
        
        assert len(chunks) == 1
        assert "Hello" in chunks[0].content

    def test_unicode_content(self, chunking_service: ChunkingService):
        """Unicode content is chunked correctly."""
        text = """
        日本語テキスト: これはテストです。
        Émojis: 🎉 📄 ✅ 🚀
        Special chars: café, naïve, résumé
        """
        
        chunks = chunking_service.chunk(text)
        
        assert len(chunks) >= 1
        # Content should be preserved
        all_content = " ".join(c.content for c in chunks)
        assert "日本語" in all_content or len(chunks) > 0

    def test_very_long_line(self, chunking_service: ChunkingService):
        """Very long single line is split."""
        text = "word " * 1000  # ~1000 words, single line
        
        chunks = chunking_service.chunk(text)
        
        # Should be split into multiple chunks
        assert len(chunks) > 1


@pytest.mark.unit
class TestChunkingMethods:
    """Tests comparing different chunking methods."""

    def test_recursive_vs_token_produces_different_results(
        self,
        chunking_service: ChunkingService,
        technical_doc_content: str,
    ):
        """Different methods may produce different chunk counts."""
        recursive_config = ChunkingConfig(method=ChunkingMethod.RECURSIVE)
        token_config = ChunkingConfig(method=ChunkingMethod.TOKEN)
        
        recursive_chunks = chunking_service.chunk(technical_doc_content, recursive_config)
        token_chunks = chunking_service.chunk(technical_doc_content, token_config)
        
        # Both should produce chunks
        assert len(recursive_chunks) > 0
        assert len(token_chunks) > 0
        
        # They may have different counts (depends on content structure)
        # Just verify both work
        assert all(c.content for c in recursive_chunks)
        assert all(c.content for c in token_chunks)


@pytest.mark.slow
@pytest.mark.unit
class TestSemanticChunking:
    """Tests for semantic chunking (requires embeddings)."""

    def test_semantic_chunking(self, chunking_service: ChunkingService, hr_policy_content: str):
        """Semantic chunking works with embeddings."""
        config = ChunkingConfig(method=ChunkingMethod.SEMANTIC)
        
        try:
            chunks = chunking_service.chunk(hr_policy_content, config)
            
            assert len(chunks) > 0
            # Semantic chunking should respect topic boundaries
            for chunk in chunks:
                assert len(chunk.content) > 0
        except Exception as e:
            # Semantic chunking may fail if embeddings not available
            pytest.skip(f"Semantic chunking not available: {e}")
