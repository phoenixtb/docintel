"""
Indexing Pipeline Tests
=======================

Tests for document embedding and storage in Qdrant.
"""

import pytest
import uuid

from src.pipelines.indexing import (
    create_document_store,
    create_indexing_pipeline,
    index_chunks,
    delete_document_vectors,
    delete_tenant_vectors,
    COLLECTION_NAME,
)
from src.chunking import ChunkingService


@pytest.mark.unit
class TestDocumentStoreCreation:
    """Tests for QdrantDocumentStore creation."""

    def test_create_document_store_default(self, qdrant_url: str):
        """Document store is created with default settings."""
        store = create_document_store(url=qdrant_url)
        
        assert store is not None
        assert store.index == COLLECTION_NAME

    def test_create_document_store_custom_collection(self, qdrant_url: str, test_collection_name: str):
        """Document store accepts custom collection name."""
        store = create_document_store(url=qdrant_url, collection=test_collection_name)
        
        assert store.index == test_collection_name


@pytest.mark.unit
class TestIndexingPipelineCreation:
    """Tests for indexing pipeline creation."""

    def test_create_indexing_pipeline(self, qdrant_url: str):
        """Indexing pipeline is created with embedder and writer."""
        store = create_document_store(url=qdrant_url)
        pipeline = create_indexing_pipeline(store)
        
        assert pipeline is not None
        assert "embedder" in pipeline.graph.nodes
        assert "writer" in pipeline.graph.nodes


@pytest.mark.integration
@pytest.mark.slow
class TestIndexChunks:
    """Tests for chunk indexing."""

    def test_index_single_chunk(
        self,
        qdrant_url: str,
        cleanup_tenant_data: str,
    ):
        """Single chunk is indexed successfully."""
        tenant_id = cleanup_tenant_data
        document_id = str(uuid.uuid4())
        
        chunks = [
            {
                "content": "This is a test chunk with some content about employee policies.",
                "metadata": {
                    "filename": "test.txt",
                    "chunk_index": 0,
                },
            }
        ]
        
        result = pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            index_chunks(
                chunks=chunks,
                tenant_id=tenant_id,
                document_id=document_id,
            )
        )
        
        assert result["embedded_count"] == 1
        assert result["collection"] == COLLECTION_NAME

    def test_index_multiple_chunks(
        self,
        qdrant_url: str,
        cleanup_tenant_data: str,
        hr_policy_content: str,
    ):
        """Multiple chunks are indexed successfully."""
        tenant_id = cleanup_tenant_data
        document_id = str(uuid.uuid4())
        
        # Create chunks from actual document
        chunking_service = ChunkingService()
        chunk_results = chunking_service.chunk_document(
            text=hr_policy_content,
            document_id=document_id,
            tenant_id=tenant_id,
            filename="hr_policy.txt",
        )
        
        # Convert to dict format
        chunks = [
            {
                "content": c.content,
                "metadata": c.metadata,
            }
            for c in chunk_results
        ]
        
        result = pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            index_chunks(
                chunks=chunks,
                tenant_id=tenant_id,
                document_id=document_id,
            )
        )
        
        assert result["embedded_count"] == len(chunks)
        assert result["embedded_count"] > 1

    def test_index_with_custom_chunk_ids(
        self,
        qdrant_url: str,
        cleanup_tenant_data: str,
    ):
        """Chunks with custom IDs are indexed correctly."""
        tenant_id = cleanup_tenant_data
        document_id = str(uuid.uuid4())
        
        chunk_id = str(uuid.uuid4())
        chunks = [
            {
                "chunk_id": chunk_id,
                "content": "Custom ID chunk content.",
                "metadata": {"filename": "test.txt"},
            }
        ]
        
        result = pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            index_chunks(
                chunks=chunks,
                tenant_id=tenant_id,
                document_id=document_id,
            )
        )
        
        assert result["embedded_count"] == 1


@pytest.mark.integration
@pytest.mark.slow
class TestDeleteVectors:
    """Tests for vector deletion."""

    def test_delete_document_vectors(
        self,
        qdrant_url: str,
        qdrant_client,
        cleanup_tenant_data: str,
    ):
        """Document vectors are deleted correctly."""
        import asyncio
        
        tenant_id = cleanup_tenant_data
        document_id = str(uuid.uuid4())
        
        # First, index some chunks
        chunks = [
            {"content": f"Chunk {i} content for deletion test.", "metadata": {}}
            for i in range(3)
        ]
        
        asyncio.get_event_loop().run_until_complete(
            index_chunks(
                chunks=chunks,
                tenant_id=tenant_id,
                document_id=document_id,
            )
        )
        
        # Verify chunks exist
        from qdrant_client.http import models
        initial_count = qdrant_client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=document_id),
                    )
                ]
            ),
        )
        assert initial_count.count == 3
        
        # Delete
        result = asyncio.get_event_loop().run_until_complete(
            delete_document_vectors(
                document_id=document_id,
                tenant_id=tenant_id,
                qdrant_url=qdrant_url,
            )
        )
        
        assert result["deleted"] is True
        
        # Verify deletion
        final_count = qdrant_client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=document_id),
                    )
                ]
            ),
        )
        assert final_count.count == 0

    def test_delete_tenant_vectors(
        self,
        qdrant_url: str,
        qdrant_client,
    ):
        """All tenant vectors are deleted correctly."""
        import asyncio
        
        # Use unique tenant for this test
        tenant_id = f"delete_test_{uuid.uuid4().hex[:8]}"
        
        # Index chunks for multiple documents
        for i in range(2):
            doc_id = str(uuid.uuid4())
            chunks = [
                {"content": f"Doc {i} chunk content.", "metadata": {}}
            ]
            asyncio.get_event_loop().run_until_complete(
                index_chunks(
                    chunks=chunks,
                    tenant_id=tenant_id,
                    document_id=doc_id,
                )
            )
        
        # Verify chunks exist
        from qdrant_client.http import models
        initial_count = qdrant_client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    )
                ]
            ),
        )
        assert initial_count.count >= 2
        
        # Delete all tenant data
        result = asyncio.get_event_loop().run_until_complete(
            delete_tenant_vectors(
                tenant_id=tenant_id,
                qdrant_url=qdrant_url,
            )
        )
        
        assert result["deleted"] is True
        
        # Verify deletion
        final_count = qdrant_client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    )
                ]
            ),
        )
        assert final_count.count == 0


@pytest.mark.integration
class TestIndexingWithDomains:
    """Tests for indexing with domain metadata."""

    def test_index_with_domain_metadata(
        self,
        qdrant_url: str,
        cleanup_tenant_data: str,
        hr_policy_content: str,
    ):
        """Chunks with domain metadata are indexed correctly."""
        import asyncio
        
        tenant_id = cleanup_tenant_data
        document_id = str(uuid.uuid4())
        
        chunking_service = ChunkingService()
        chunk_results = chunking_service.chunk_document(
            text=hr_policy_content,
            document_id=document_id,
            tenant_id=tenant_id,
            filename="hr_policy.txt",
            extra_metadata={
                "domain": "hr_policy",
                "document_type": "hr_policy",
            },
        )
        
        chunks = [
            {"content": c.content, "metadata": c.metadata}
            for c in chunk_results
        ]
        
        result = asyncio.get_event_loop().run_until_complete(
            index_chunks(
                chunks=chunks,
                tenant_id=tenant_id,
                document_id=document_id,
            )
        )
        
        assert result["embedded_count"] == len(chunks)


@pytest.mark.integration
@pytest.mark.slow
class TestIndexAllDocuments:
    """Tests for indexing all test documents."""

    def test_index_all_test_documents(
        self,
        qdrant_url: str,
        qdrant_client,
        all_documents: dict,
    ):
        """All test documents are indexed successfully."""
        import asyncio
        
        # Use unique tenant for this test
        tenant_id = f"all_docs_test_{uuid.uuid4().hex[:8]}"
        chunking_service = ChunkingService()
        
        total_chunks = 0
        
        for doc_key, doc_info in all_documents.items():
            chunk_results = chunking_service.chunk_document(
                text=doc_info["content"],
                document_id=doc_info["document_id"],
                tenant_id=tenant_id,
                filename=doc_info["filename"],
                extra_metadata={
                    "domain": doc_info["domain"],
                    "document_type": doc_info["domain"],
                },
            )
            
            chunks = [
                {"content": c.content, "metadata": c.metadata}
                for c in chunk_results
            ]
            
            result = asyncio.get_event_loop().run_until_complete(
                index_chunks(
                    chunks=chunks,
                    tenant_id=tenant_id,
                    document_id=doc_info["document_id"],
                )
            )
            
            total_chunks += result["embedded_count"]
        
        # Verify total count
        from qdrant_client.http import models
        count = qdrant_client.count(
            collection_name=COLLECTION_NAME,
            count_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="tenant_id",
                        match=models.MatchValue(value=tenant_id),
                    )
                ]
            ),
        )
        
        assert count.count == total_chunks
        assert total_chunks > 10  # Should have substantial content
        
        # Cleanup
        asyncio.get_event_loop().run_until_complete(
            delete_tenant_vectors(tenant_id=tenant_id)
        )
