"""
Direct PostgreSQL access for ingestion-service.

Writes chunks and updates document status. Uses SQLAlchemy Core (not ORM)
to stay lightweight and avoid JPA model coupling.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text

from .config import get_settings

logger = logging.getLogger(__name__)

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    tenant_id: str
    content: str
    chunk_index: int
    start_char: int
    end_char: int
    token_count: int
    metadata: dict[str, Any]


def persist_chunks(chunks: list[ChunkRecord]) -> int:
    """
    Upsert chunks into the chunks table.
    Returns number of rows inserted/updated.
    """
    if not chunks:
        return 0

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO chunks (
                    id, document_id, tenant_id, content,
                    chunk_index, start_char, end_char, token_count, metadata
                ) VALUES (
                    :id, :document_id, :tenant_id, :content,
                    :chunk_index, :start_char, :end_char, :token_count, :metadata::jsonb
                )
                ON CONFLICT (id) DO UPDATE SET
                    content     = EXCLUDED.content,
                    chunk_index = EXCLUDED.chunk_index,
                    start_char  = EXCLUDED.start_char,
                    end_char    = EXCLUDED.end_char,
                    token_count = EXCLUDED.token_count,
                    metadata    = EXCLUDED.metadata
                """
            ),
            [
                {
                    "id": c.chunk_id,
                    "document_id": c.document_id,
                    "tenant_id": c.tenant_id,
                    "content": c.content,
                    "chunk_index": c.chunk_index,
                    "start_char": c.start_char,
                    "end_char": c.end_char,
                    "token_count": c.token_count,
                    "metadata": json.dumps(c.metadata),
                }
                for c in chunks
            ],
        )

    logger.info("Persisted %d chunks for document %s", len(chunks), chunks[0].document_id if chunks else "?")
    return len(chunks)


def update_document_status(
    document_id: str,
    status: str,
    chunk_count: int = 0,
    error_message: str | None = None,
) -> None:
    """Update document processing status directly in PostgreSQL."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE documents
                SET status        = :status,
                    chunk_count   = :chunk_count,
                    error_message = :error_message,
                    updated_at    = NOW()
                WHERE id = :document_id
                """
            ),
            {
                "status": status.upper(),
                "chunk_count": chunk_count,
                "error_message": error_message,
                "document_id": document_id,
            },
        )


def delete_document_chunks(document_id: str) -> int:
    """Delete all chunks for a document. Returns rows deleted."""
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM chunks WHERE document_id = :document_id"),
            {"document_id": document_id},
        )
        return result.rowcount


def delete_tenant_chunks(tenant_id: str) -> int:
    """Delete all chunks for a tenant. Returns rows deleted."""
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM chunks WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        return result.rowcount
