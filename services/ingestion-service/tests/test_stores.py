"""
Unit tests for src/stores.py.

All tests use unittest.mock — no live Qdrant instance required.
We patch QdrantDocumentStore and QdrantClient at the module level to verify:
  1. _ensure_acl_indexes reads payload_schema.keys() (not .values())
  2. Existing indexes are skipped
  3. Missing indexes are created
  4. The store's internal client is reused, not a new QdrantClient instance
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from src.stores import _ACL_INDEXES, _ensure_acl_indexes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_client(existing_field_names: list[str]) -> MagicMock:
    """
    Build a mock QdrantClient whose get_collection().payload_schema
    behaves like a real dict with the given field names as keys.
    """
    payload_schema = {name: MagicMock() for name in existing_field_names}
    collection_info = MagicMock()
    collection_info.payload_schema = payload_schema
    mock_client = MagicMock()
    mock_client.get_collection.return_value = collection_info
    return mock_client


# ---------------------------------------------------------------------------
# _ensure_acl_indexes correctness
# ---------------------------------------------------------------------------


def test_uses_payload_schema_keys_not_values() -> None:
    """
    get_collection().payload_schema must be read via .keys(), not .values().
    We verify this by constructing a schema where .keys() and .values() would
    give different results and checking that the correct field names are found.
    """
    # One ACL field already exists
    existing_field = _ACL_INDEXES[0][0]   # e.g. "meta.classification"
    mock_client = _make_mock_client([existing_field])

    _ensure_acl_indexes(mock_client, "test_collection")

    # Because we use keys(), the existing field must NOT be re-created
    calls = [c[1]["field_name"] for c in mock_client.create_payload_index.call_args_list]
    assert existing_field not in calls, (
        f"Field '{existing_field}' should be skipped — it's already in payload_schema.keys()"
    )


def test_skips_all_fields_when_all_already_indexed() -> None:
    """When all 7 ACL fields are in payload_schema, create_payload_index is never called."""
    all_field_names = [field for field, _ in _ACL_INDEXES]
    mock_client = _make_mock_client(all_field_names)

    _ensure_acl_indexes(mock_client, "test_collection")

    mock_client.create_payload_index.assert_not_called()


def test_creates_all_missing_indexes_when_schema_is_empty() -> None:
    """When payload_schema is empty, all 7 ACL indexes must be created."""
    mock_client = _make_mock_client([])  # no existing indexes

    _ensure_acl_indexes(mock_client, "test_collection")

    assert mock_client.create_payload_index.call_count == len(_ACL_INDEXES)
    created_fields = {c[1]["field_name"] for c in mock_client.create_payload_index.call_args_list}
    expected_fields = {field for field, _ in _ACL_INDEXES}
    assert created_fields == expected_fields


def test_creates_only_missing_indexes_when_some_exist() -> None:
    """3 existing, 4 missing → exactly 4 create_payload_index calls."""
    all_fields = [field for field, _ in _ACL_INDEXES]
    existing = all_fields[:3]
    missing = all_fields[3:]
    mock_client = _make_mock_client(existing)

    _ensure_acl_indexes(mock_client, "test_collection")

    assert mock_client.create_payload_index.call_count == len(missing)
    created_fields = {c[1]["field_name"] for c in mock_client.create_payload_index.call_args_list}
    assert created_fields == set(missing)


# ---------------------------------------------------------------------------
# Store client reuse (low-15)
# ---------------------------------------------------------------------------


def test_get_document_store_uses_store_client_not_new_qdrant_client() -> None:
    """
    get_document_store() must NOT instantiate a raw QdrantClient to call
    _ensure_acl_indexes. Instead it reuses store.client (the internal client
    already open inside QdrantDocumentStore).
    """
    mock_internal_client = _make_mock_client([])  # empty schema → triggers creates

    mock_store = MagicMock()
    mock_store.client = mock_internal_client

    with patch("src.stores.QdrantDocumentStore", return_value=mock_store) as mock_store_cls, \
         patch("src.stores.QdrantClient") as mock_qdrant_client_cls, \
         patch("src.stores._store_cache", {}), \
         patch("src.stores.get_settings") as mock_settings:

        mock_settings.return_value = MagicMock(
            qdrant_url="http://qdrant:6333",
            qdrant_api_key=None,
        )

        from src.stores import get_document_store
        get_document_store("test-tenant", mock_settings.return_value)

    # The raw QdrantClient constructor must NOT have been called (no second connection)
    mock_qdrant_client_cls.assert_not_called()
    # store.client (the internal one) must have been used for index creation
    mock_internal_client.get_collection.assert_called_once()
