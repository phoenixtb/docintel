"""
Unit tests for HuggingFaceAdapter and _load_dataset_sync.

HuggingFace `datasets.load_dataset` is always mocked — no network calls or
real dataset downloads occur in these tests.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.huggingface import (
    DATASET_CONFIGS,
    HuggingFaceAdapter,
    _load_dataset_sync,
    _safe_filename,
)
from src.adapters.base import LoadedFile


# ---------------------------------------------------------------------------
# _safe_filename
# ---------------------------------------------------------------------------


def test_safe_filename_sanitises_special_chars():
    result = _safe_filename("Contract: ABC/XYZ #1!", "fallback")
    assert "/" not in result
    assert ":" not in result
    assert result.endswith(".txt")


def test_safe_filename_uses_fallback_for_empty_title():
    result = _safe_filename("", "fallback")
    assert result == "fallback.txt"


def test_safe_filename_truncates_long_titles():
    long_title = "A" * 200
    result = _safe_filename(long_title, "fallback")
    assert len(result) <= 125  # 120 chars + ".txt"


# ---------------------------------------------------------------------------
# Dataset configs
# ---------------------------------------------------------------------------


def test_dataset_configs_contain_expected_keys():
    assert set(DATASET_CONFIGS.keys()) == {"techqa", "hr_policies", "cuad"}


def test_cuad_config_has_dedup_field():
    assert DATASET_CONFIGS["cuad"]["dedup_field"] == "title"


def test_hr_policies_config_has_extract_fn():
    assert DATASET_CONFIGS["hr_policies"]["extract_fn"] == "messages"


def test_techqa_config_has_no_dedup():
    assert DATASET_CONFIGS["techqa"]["dedup_field"] is None


# ---------------------------------------------------------------------------
# HuggingFaceAdapter.fetch — unknown dataset
# ---------------------------------------------------------------------------


def test_fetch_unknown_dataset_raises_value_error():
    adapter = HuggingFaceAdapter()
    with pytest.raises(ValueError, match="Unknown dataset"):
        list(adapter.fetch({"dataset_key": "nonexistent"}, "tenant-x"))


# ---------------------------------------------------------------------------
# _load_dataset_sync — CUAD dedup path
# ---------------------------------------------------------------------------


def _make_cuad_rows(n_unique: int, rows_per_title: int = 3):
    rows = []
    for i in range(n_unique):
        title = f"Contract {i:04d}"
        for _ in range(rows_per_title):
            rows.append(
                {
                    "title": title,
                    "context": f"This is contract content for {title}. " * 10,
                }
            )
    return rows


def test_cuad_dedup_returns_n_unique_documents():
    rows = _make_cuad_rows(n_unique=10, rows_per_title=5)
    ds = MagicMock()
    ds.__iter__ = MagicMock(side_effect=lambda: iter(rows))
    ds.unique = MagicMock(return_value=[f"Contract {i:04d}" for i in range(10)])

    cfg = {**DATASET_CONFIGS["cuad"], "_key": "cuad"}

    with patch("datasets.load_dataset", return_value=ds):
        results = _load_dataset_sync(cfg, samples=5, tenant_id="t1")

    assert len(results) == 5
    titles = {r.metadata["contract_title"] for r in results}
    assert len(titles) == 5  # all unique


def test_cuad_dedup_skips_short_content():
    rows = [
        {"title": "Contract A", "context": "short"},
        {"title": "Contract B", "context": "This is substantial contract content. " * 5},
    ]
    ds = MagicMock()
    ds.__iter__ = MagicMock(side_effect=lambda: iter(rows))
    ds.unique = MagicMock(return_value=["Contract A", "Contract B"])

    cfg = {**DATASET_CONFIGS["cuad"], "_key": "cuad"}

    with patch("datasets.load_dataset", return_value=ds):
        results = _load_dataset_sync(cfg, samples=5, tenant_id="t2")

    # Only "Contract B" has sufficient content
    assert len(results) == 1
    assert results[0].metadata["contract_title"] == "Contract B"


# ---------------------------------------------------------------------------
# _load_dataset_sync — messages (hr_policies) path
# ---------------------------------------------------------------------------


def _make_hr_row(idx: int, content_len: int = 200):
    return {
        "messages": [
            {"role": "user", "content": "A" * content_len},
            {"role": "assistant", "content": "B" * content_len},
        ]
    }


def test_hr_policies_extracts_messages():
    rows = [_make_hr_row(i) for i in range(5)]
    ds = MagicMock()
    ds.__iter__ = MagicMock(side_effect=lambda: iter(rows))

    cfg = {**DATASET_CONFIGS["hr_policies"], "_key": "hr_policies"}

    with patch("datasets.load_dataset", return_value=ds):
        results = _load_dataset_sync(cfg, samples=3, tenant_id="t3")

    assert len(results) == 3
    for r in results:
        assert "USER:" in r.content.decode()
        assert "ASSISTANT:" in r.content.decode()


def test_hr_policies_skips_non_list_messages():
    rows = [
        {"messages": "not a list"},
        {"messages": [{"role": "user", "content": "Q " * 50}, {"role": "assistant", "content": "A " * 50}]},
    ]
    ds = MagicMock()
    ds.__iter__ = MagicMock(side_effect=lambda: iter(rows))

    cfg = {**DATASET_CONFIGS["hr_policies"], "_key": "hr_policies"}

    with patch("datasets.load_dataset", return_value=ds):
        results = _load_dataset_sync(cfg, samples=5, tenant_id="t4")

    assert len(results) == 1


# ---------------------------------------------------------------------------
# _load_dataset_sync — simple scan (techqa) path
# ---------------------------------------------------------------------------


def test_techqa_simple_scan_respects_sample_limit():
    rows = [{"text": f"Technical document content row {i}. " * 5} for i in range(20)]
    ds = MagicMock()
    ds.__iter__ = MagicMock(side_effect=lambda: iter(rows))

    cfg = {**DATASET_CONFIGS["techqa"], "_key": "techqa"}

    with patch("datasets.load_dataset", return_value=ds):
        results = _load_dataset_sync(cfg, samples=5, tenant_id="t5")

    assert len(results) == 5


def test_techqa_skips_short_text():
    rows = [
        {"text": "short"},
        {"text": "This is a long enough technical document. " * 3},
    ]
    ds = MagicMock()
    ds.__iter__ = MagicMock(side_effect=lambda: iter(rows))

    cfg = {**DATASET_CONFIGS["techqa"], "_key": "techqa"}

    with patch("datasets.load_dataset", return_value=ds):
        results = _load_dataset_sync(cfg, samples=5, tenant_id="t6")

    assert len(results) == 1


# ---------------------------------------------------------------------------
# LoadedFile shape
# ---------------------------------------------------------------------------


def test_loaded_file_has_expected_metadata_fields():
    rows = [{"text": "Content that is definitely long enough for testing. " * 3}]
    ds = MagicMock()
    ds.__iter__ = MagicMock(side_effect=lambda: iter(rows))

    cfg = {**DATASET_CONFIGS["techqa"], "_key": "techqa"}

    with patch("datasets.load_dataset", return_value=ds):
        results = _load_dataset_sync(cfg, samples=1, tenant_id="meta-tenant")

    assert len(results) == 1
    lf = results[0]
    assert isinstance(lf, LoadedFile)
    assert lf.metadata["source"] == "sample_dataset"
    assert lf.metadata["dataset_key"] == "techqa"
    assert lf.metadata["tenant_id"] == "meta-tenant"
    assert lf.filename.endswith(".txt")
