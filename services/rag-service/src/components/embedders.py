"""
BM25 Sparse Embedding Components
==================================

Haystack @components for computing BM25 sparse embeddings via fastembed.
Designed to pair with QdrantDocumentStore(use_sparse_embeddings=True) and
QdrantHybridRetriever for idiomatic Haystack hybrid search.
"""

import logging

from haystack import Document, component

logger = logging.getLogger(__name__)
from haystack.dataclasses import SparseEmbedding
from typing import Optional


@component
class BM25SparseDocumentEmbedder:
    """
    Computes BM25 sparse embeddings for documents and populates
    Document.sparse_embedding so DocumentWriter stores them in Qdrant.

    Designed to sit before OllamaDocumentEmbedder in the indexing pipeline:
      BM25SparseDocumentEmbedder → OllamaDocumentEmbedder → DocumentWriter
    """

    def __init__(self, model_name: str = "Qdrant/bm25"):
        self.model_name = model_name
        self._model = None

    def warm_up(self):
        from fastembed import SparseTextEmbedding
        self._model = SparseTextEmbedding(model_name=self.model_name)

    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        """
        Compute BM25 sparse embeddings for a list of documents.
        Sets doc.sparse_embedding on each; falls back gracefully on error.
        """
        if self._model is None:
            self.warm_up()

        texts = [doc.content or "" for doc in documents]
        try:
            sparse_embeddings = list(self._model.passage_embed(texts))  # type: ignore[union-attr]
            for doc, se in zip(documents, sparse_embeddings):
                doc.sparse_embedding = SparseEmbedding(
                    indices=se.indices.tolist(),
                    values=se.values.tolist(),
                )
        except Exception as e:
            logger.warning("BM25 sparse document embedding failed, skipping sparse vectors: %s", e)

        return {"documents": documents}


@component
class BM25SparseTextEmbedder:
    """
    Computes BM25 sparse embedding for a query string.

    Designed to run before SecureRetriever in the query flow:
      BM25SparseTextEmbedder → SecureRetriever (→ QdrantHybridRetriever)
    """

    def __init__(self, model_name: str = "Qdrant/bm25"):
        self.model_name = model_name
        self._model = None

    def warm_up(self):
        from fastembed import SparseTextEmbedding
        self._model = SparseTextEmbedding(model_name=self.model_name)

    @component.output_types(sparse_embedding=Optional[SparseEmbedding])
    def run(self, text: str) -> dict:
        """
        Compute BM25 sparse embedding for a query string.
        Returns None sparse_embedding on failure (falls back to dense-only).
        """
        if self._model is None:
            self.warm_up()

        try:
            result = list(self._model.query_embed([text]))  # type: ignore[union-attr]
            if result:
                se = result[0]
                return {
                    "sparse_embedding": SparseEmbedding(
                        indices=se.indices.tolist(),
                        values=se.values.tolist(),
                    )
                }
        except Exception as e:
            logger.warning("BM25 sparse query embedding failed, using dense-only retrieval: %s", e)

        return {"sparse_embedding": None}
