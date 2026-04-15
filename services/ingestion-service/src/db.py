"""
DEPRECATED — ingestion-service no longer writes directly to PostgreSQL.

All chunk persistence now goes through document-service's
POST /internal/documents/{id}/chunks/bulk API, which is the single
owner of the documents schema. See document_client.py.

This module is kept as an empty stub to avoid import errors from any
code paths not yet updated. It will be removed in Phase 5 cleanup.
"""
