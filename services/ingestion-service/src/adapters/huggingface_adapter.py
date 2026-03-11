"""
HuggingFace dataset adapter.

Loads sample documents from HuggingFace Hub datasets and writes them as
local .txt temp files so DoclingConverter can process them through the same
Haystack pipeline as real documents.

Dataset config and extraction logic moved from rag-service/src/datasets.py.
"""

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .base import SourceAdapter

logger = logging.getLogger(__name__)

DATASET_CONFIGS: dict[str, dict] = {
    "techqa": {
        "name": "m-ric/huggingface_doc",
        "subset": None,
        "split": "train",
        "domain": "technical",
        "text_field": "text",
        "is_list_field": False,
        "extract_fn": None,
    },
    "hr_policies": {
        "name": "syncora/hr-policies-qa-dataset",
        "subset": None,
        "split": "train",
        "domain": "hr_policy",
        "text_field": "messages",
        "is_list_field": False,
        "extract_fn": "messages",
    },
    "cuad": {
        "name": "Nadav-Timor/CUAD",
        "subset": None,
        "split": "train",
        "domain": "contracts",
        "text_field": "context",
        "is_list_field": False,
        "extract_fn": "cuad_dedupe",
    },
}


@dataclass
class SampledText:
    content: str
    domain: str
    source_dataset: str
    metadata: dict


def _extract_texts(dataset_key: str, samples: int, tenant_id: str) -> list[SampledText]:
    """Synchronous HuggingFace load (run in executor to avoid blocking the loop)."""
    from datasets import load_dataset

    if dataset_key not in DATASET_CONFIGS:
        raise ValueError(f"Unknown dataset: {dataset_key}. Valid: {list(DATASET_CONFIGS)}")

    config = DATASET_CONFIGS[dataset_key]

    if config["subset"]:
        ds = load_dataset(config["name"], config["subset"], split=config["split"])
    else:
        ds = load_dataset(config["name"], split=config["split"])

    if len(ds) > samples * 3:  # oversample to account for deduplication / filtering
        ds = ds.shuffle(seed=42).select(range(min(samples * 3, len(ds))))

    results: list[SampledText] = []
    seen_titles: set[str] = set()
    extract_fn = config.get("extract_fn")

    for idx, item in enumerate(ds):
        text_field = config["text_field"]
        text: str | None = None

        if extract_fn == "cuad_dedupe":
            title = item.get("title", "")
            if title in seen_titles:
                continue
            seen_titles.add(title)
            text = item.get(text_field, "")
        elif extract_fn == "messages":
            messages = item.get(text_field, [])
            if isinstance(messages, list):
                parts = [
                    f"{msg.get('role', '').upper()}: {msg.get('content', '')}"
                    for msg in messages
                    if isinstance(msg, dict) and msg.get("role") in ("user", "assistant")
                ]
                text = "\n".join(parts)
        elif config["is_list_field"]:
            for doc_idx, doc_text in enumerate((item.get(text_field, []) or [])[:3]):
                if doc_text and len(doc_text.strip()) > 50:
                    results.append(
                        SampledText(
                            content=doc_text.strip(),
                            domain=config["domain"],
                            source_dataset=dataset_key,
                            metadata={
                                "source": dataset_key,
                                "item_index": idx,
                                "doc_index": doc_idx,
                                "tenant_id": tenant_id,
                                "domain": config["domain"],
                            },
                        )
                    )
            if len(results) >= samples:
                break
            continue
        else:
            text = item.get(text_field, "")

        if text and len(text.strip()) > 50:
            extra: dict = {}
            if extract_fn == "cuad_dedupe":
                extra["contract_title"] = item.get("title", "")
            results.append(
                SampledText(
                    content=text.strip(),
                    domain=config["domain"],
                    source_dataset=dataset_key,
                    metadata={
                        "source": dataset_key,
                        "item_index": idx,
                        "tenant_id": tenant_id,
                        "domain": config["domain"],
                        **extra,
                    },
                )
            )

        if len(results) >= samples:
            break

    return results[:samples]


class HuggingFaceAdapter(SourceAdapter):
    """
    Load sample texts from HuggingFace and write them as local .txt files.

    source_ref keys:
        dataset_key (str): One of 'techqa', 'hr_policies', 'cuad'
        samples     (int): Number of documents to load
        tenant_id   (str): Tenant ID for metadata
    """

    def source_type(self) -> str:
        return "hf"

    async def fetch(self, source_ref: dict) -> list[Path]:
        dataset_key = source_ref["dataset_key"]
        samples = int(source_ref.get("samples", 10))
        tenant_id = source_ref.get("tenant_id", "default")

        loop = asyncio.get_running_loop()
        texts: list[SampledText] = await loop.run_in_executor(
            None, _extract_texts, dataset_key, samples, tenant_id
        )

        tmp_dir = Path(tempfile.mkdtemp(prefix=f"ingest_hf_{dataset_key}_"))
        paths: list[Path] = []

        for i, doc in enumerate(texts):
            file_path = tmp_dir / f"{dataset_key}_{i:04d}.txt"
            file_path.write_text(doc.content, encoding="utf-8")
            paths.append(file_path)

        logger.info("HuggingFace adapter: %d files written for dataset '%s'", len(paths), dataset_key)
        return paths
