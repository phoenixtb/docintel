"""
HuggingFace dataset adapter.

Loads sample documents from HuggingFace Hub datasets and yields LoadedFile objects.
Uses Arrow-fast ds.unique() dedup for datasets like CUAD that have multiple rows
per unique document (e.g. many QA pairs per contract title).
"""

import logging
from typing import Iterator

from .base import LoadedFile, SourceAdapter

logger = logging.getLogger(__name__)

# Dataset configurations.
# dedup_field: when set, ds.unique(dedup_field) is used to pick N unique documents.
#   This is Arrow-native and avoids the 3x-oversample heuristic that breaks for
#   datasets with 50-100 rows per unique document (e.g. CUAD).
# title_field: field to use as the document filename (sanitized).
DATASET_CONFIGS: dict[str, dict] = {
    "techqa": {
        "hf_name": "m-ric/huggingface_doc",
        "split": "train",
        "domain": "technical",
        "text_field": "text",
        "dedup_field": None,
        "title_field": None,
        "extract_fn": None,
    },
    "hr_policies": {
        "hf_name": "syncora/hr-policies-qa-dataset",
        "split": "train",
        "domain": "hr_policy",
        "text_field": "messages",
        "dedup_field": None,
        "title_field": None,
        "extract_fn": "messages",
    },
    "cuad": {
        "hf_name": "Nadav-Timor/CUAD",
        "split": "train",
        "domain": "contracts",
        "text_field": "context",
        "dedup_field": "title",   # ds.unique('title')[:N] — Arrow-fast, no multiplier guessing
        "title_field": "title",
        "extract_fn": None,
    },
}


def _safe_filename(title: str, fallback: str) -> str:
    """Sanitize a title for use as a filename."""
    if not title:
        return f"{fallback}.txt"
    safe = "".join(c if (c.isalnum() or c in " -_()") else "_" for c in title)
    return f"{safe[:120].strip()}.txt"


def _load_dataset_sync(config: dict, samples: int, tenant_id: str) -> list[LoadedFile]:
    """
    Synchronous HuggingFace load — run in executor to avoid blocking the event loop.

    CUAD dedup strategy:
      1. ds.unique('title') returns Arrow-native list of unique values (fast).
      2. Take first `samples` unique titles — deterministic, no shuffle needed.
      3. Iterate the full dataset once; for each row, if its title is in the
         selected set and not yet seen, yield it and mark as seen.
      4. Early-exit when all selected titles have been found.

    This avoids the brittle 3x oversample heuristic. CUAD has ~50-100 rows per
    unique title; the old approach capped the scan too early.
    """
    from datasets import load_dataset  # deferred import — heavy dependency

    hf_name = config["hf_name"]
    split = config["split"]
    text_field = config["text_field"]
    dedup_field = config.get("dedup_field")
    title_field = config.get("title_field")
    extract_fn = config.get("extract_fn")
    domain = config["domain"]

    ds = load_dataset(hf_name, split=split)
    results: list[LoadedFile] = []

    if dedup_field:
        # Arrow-native unique — returns a Python list, not a dataset
        unique_values: list = ds.unique(dedup_field)[:samples]
        selected: set = set(unique_values)
        seen: set = set()

        for row in ds:
            key = row[dedup_field]
            if key in selected and key not in seen:
                seen.add(key)
                text = row.get(text_field, "") or ""
                if len(text.strip()) < 50:
                    continue
                filename = _safe_filename(
                    row.get(title_field or dedup_field, ""), f"{domain}_{len(results):04d}"
                )
                results.append(LoadedFile(
                    content=text.strip().encode("utf-8"),
                    filename=filename,
                    metadata={
                        "source": "sample_dataset",
                        "dataset_key": config.get("_key", "unknown"),
                        "domain": domain,
                        "tenant_id": tenant_id,
                        "contract_title": key,
                    },
                ))
            if len(seen) >= len(selected):
                break

    elif extract_fn == "messages":
        for idx, row in enumerate(ds):
            if len(results) >= samples:
                break
            messages = row.get(text_field, []) or []
            if not isinstance(messages, list):
                continue
            parts = [
                f"{msg.get('role', '').upper()}: {msg.get('content', '')}"
                for msg in messages
                if isinstance(msg, dict) and msg.get("role") in ("user", "assistant")
            ]
            text = "\n".join(parts).strip()
            if len(text) < 50:
                continue
            results.append(LoadedFile(
                content=text.encode("utf-8"),
                filename=f"{domain}_{idx:04d}.txt",
                metadata={
                    "source": "sample_dataset",
                    "dataset_key": config.get("_key", "unknown"),
                    "domain": domain,
                    "tenant_id": tenant_id,
                },
            ))

    else:
        # Simple sequential scan — take first `samples` rows with sufficient content
        for idx, row in enumerate(ds):
            if len(results) >= samples:
                break
            text = (row.get(text_field) or "").strip()
            if len(text) < 50:
                continue
            title = row.get(title_field or "", "") if title_field else ""
            filename = _safe_filename(title, f"{domain}_{idx:04d}") if title else f"{domain}_{idx:04d}.txt"
            results.append(LoadedFile(
                content=text.encode("utf-8"),
                filename=filename,
                metadata={
                    "source": "sample_dataset",
                    "dataset_key": config.get("_key", "unknown"),
                    "domain": domain,
                    "tenant_id": tenant_id,
                },
            ))

    logger.info(
        "HuggingFaceAdapter: loaded %d files for dataset '%s' (tenant=%s)",
        len(results), config.get("_key", "?"), tenant_id,
    )
    return results


class HuggingFaceAdapter(SourceAdapter):
    """
    Load sample documents from HuggingFace Hub.

    config keys:
        dataset_key (str): One of 'techqa', 'hr_policies', 'cuad'
        samples     (int): Number of unique documents to load
    """

    def source_type(self) -> str:
        return "huggingface"

    def fetch(self, config: dict, tenant_id: str) -> Iterator[LoadedFile]:
        dataset_key: str = config["dataset_key"]
        samples: int = int(config.get("samples", 10))

        if dataset_key not in DATASET_CONFIGS:
            raise ValueError(
                f"Unknown dataset: '{dataset_key}'. Valid keys: {list(DATASET_CONFIGS)}"
            )

        cfg = {**DATASET_CONFIGS[dataset_key], "_key": dataset_key}
        yield from _load_dataset_sync(cfg, samples, tenant_id)
