"""
Sample Datasets & Domain Classification
========================================

Provides:
- HuggingFace dataset loading for sample data
- Zero-shot domain classification
"""

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Domain Labels
# =============================================================================

DOMAIN_LABELS = ["hr_policy", "technical", "contracts", "general"]

DATASET_CONFIGS = {
    "techqa": {
        "name": "m-ric/huggingface_doc",
        "subset": None,
        "split": "train",
        "domain": "technical",
        "text_field": "text",   # Full documentation page text
        "is_list_field": False,
        "extract_fn": None,
    },
    "hr_policies": {
        "name": "syncora/hr-policies-qa-dataset",
        "subset": None,
        "split": "train",
        "domain": "hr_policy",
        "text_field": "messages",  # List of chat messages
        "is_list_field": False,
        "extract_fn": "messages",  # Special extraction for chat format
    },
    "cuad": {
        "name": "Nadav-Timor/CUAD",   # Parquet-native mirror of theatticusproject/cuad-qa
        "subset": None,
        "split": "train",
        "domain": "contracts",
        "text_field": "context",   # Full contract clause text
        "is_list_field": False,
        "extract_fn": "cuad_dedupe",  # Deduplicate by contract title
    },
}


# =============================================================================
# Domain Classifier
# =============================================================================

@dataclass
class ClassificationResult:
    domain: str
    confidence: float
    all_scores: dict[str, float]


class DomainClassifier:
    """Zero-shot domain classifier using DeBERTa."""

    def __init__(self, model_name: str = "MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33"):
        self.model_name = model_name
        self._classifier = None

    def _get_classifier(self):
        """Lazy load the classifier pipeline."""
        if self._classifier is None:
            from transformers import pipeline

            self._classifier = pipeline(
                "zero-shot-classification",
                model=self.model_name,
                device="cpu",  # Use CPU for consistency
            )
        return self._classifier

    def classify(self, text: str, labels: list[str] | None = None) -> ClassificationResult:
        """
        Classify text into one of the domain labels.

        Args:
            text: Text to classify (first 1000 chars used for efficiency)
            labels: Custom labels or use default DOMAIN_LABELS

        Returns:
            ClassificationResult with domain, confidence, and all scores
        """
        if labels is None:
            labels = DOMAIN_LABELS

        # Truncate for efficiency
        text = text[:1000]

        classifier = self._get_classifier()
        result = classifier(text, candidate_labels=labels, multi_label=False)

        # Build scores dict
        all_scores = {
            label: score
            for label, score in zip(result["labels"], result["scores"])
        }

        return ClassificationResult(
            domain=result["labels"][0],
            confidence=result["scores"][0],
            all_scores=all_scores,
        )


# Singleton instance
_classifier: Optional[DomainClassifier] = None


def get_domain_classifier() -> DomainClassifier:
    """Get or create the domain classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = DomainClassifier()
    return _classifier


# =============================================================================
# Dataset Loader
# =============================================================================

@dataclass
class LoadedDocument:
    content: str
    domain: str
    source_dataset: str
    metadata: dict


class DatasetLoader:
    """Load sample documents from HuggingFace datasets."""

    def __init__(self):
        self._datasets_cache: dict = {}

    def load_dataset(
        self,
        dataset_key: str,
        samples: int = 10,
        tenant_id: str = "default",
    ) -> list[LoadedDocument]:
        """
        Load sample documents from a dataset.

        Args:
            dataset_key: One of 'techqa', 'hr_policies', 'cuad'
            samples: Number of samples to load
            tenant_id: Tenant ID for metadata

        Returns:
            List of LoadedDocument objects
        """
        from datasets import load_dataset

        if dataset_key not in DATASET_CONFIGS:
            raise ValueError(f"Unknown dataset: {dataset_key}. Valid: {list(DATASET_CONFIGS.keys())}")

        config = DATASET_CONFIGS[dataset_key]

        # Load from HuggingFace
        logger.debug("Loading dataset: %s, subset: %s, split: %s", config['name'], config.get('subset'), config['split'])
        
        if config["subset"]:
            ds = load_dataset(config["name"], config["subset"], split=config["split"])
        else:
            ds = load_dataset(config["name"], split=config["split"])

        logger.debug("Dataset %s has %d items, columns: %s", dataset_key, len(ds), ds.column_names)

        # Take only requested samples
        if len(ds) > samples:
            ds = ds.shuffle(seed=42).select(range(samples))

        logger.debug("After sampling: %d items", len(ds))

        documents = []
        extract_fn = config.get("extract_fn")
        seen_titles: set[str] = set()  # for cuad_dedupe

        for idx, item in enumerate(ds):
            text_field = config["text_field"]
            
            # Debug first item
            if idx == 0:
                logger.debug("First item keys: %s", list(item.keys()))
                logger.debug("text_field '%s' extract_fn: %s", text_field, extract_fn)

            text = None
            
            # Handle special extraction functions
            if extract_fn == "cuad_dedupe":
                # CUAD-QA: 22k items across 510 contracts — deduplicate by title
                # so each contract appears once with its full context text.
                title = item.get("title", "")
                if title in seen_titles:
                    continue
                seen_titles.add(title)
                text = item.get(text_field, "")
            elif extract_fn == "messages":
                # Extract from chat messages format (HR policies)
                messages = item.get(text_field, [])
                if isinstance(messages, list):
                    # Combine question and answer for context
                    parts = []
                    for msg in messages:
                        if isinstance(msg, dict) and msg.get("role") in ["user", "assistant"]:
                            parts.append(f"{msg.get('role', '').upper()}: {msg.get('content', '')}")
                    text = "\n".join(parts)
            elif config["is_list_field"]:
                # For datasets like ragbench where documents is a list
                texts = item.get(text_field, [])
                if isinstance(texts, list):
                    for doc_idx, doc_text in enumerate(texts[:3]):  # Take first 3 docs per item
                        if doc_text and len(doc_text.strip()) > 50:
                            documents.append(LoadedDocument(
                                content=doc_text.strip(),
                                domain=config["domain"],
                                source_dataset=dataset_key,
                                metadata={
                                    "source": dataset_key,
                                    "item_index": idx,
                                    "doc_index": doc_idx,
                                    "tenant_id": tenant_id,
                                    "domain": config["domain"],
                                    "document_type": config["domain"],
                                },
                            ))
                    continue  # Skip the rest for list fields
            else:
                # Single text field
                text = item.get(text_field, "")
            
            # Add document if we have valid text
            if text and len(text.strip()) > 50:
                extra_meta: dict = {}
                if extract_fn == "cuad_dedupe":
                    extra_meta["contract_title"] = item.get("title", "")
                documents.append(LoadedDocument(
                    content=text.strip(),
                    domain=config["domain"],
                    source_dataset=dataset_key,
                    metadata={
                        "source": dataset_key,
                        "item_index": idx,
                        "tenant_id": tenant_id,
                        "domain": config["domain"],
                        "document_type": config["domain"],
                        **extra_meta,
                    },
                ))

            # Stop if we have enough
            if len(documents) >= samples:
                break

        logger.debug("Extracted %d documents from %s", len(documents), dataset_key)
        return documents[:samples]


# Singleton instance for DatasetLoader
_dataset_loader: Optional[DatasetLoader] = None


def get_dataset_loader() -> DatasetLoader:
    """Get or create the dataset loader singleton instance."""
    global _dataset_loader
    if _dataset_loader is None:
        _dataset_loader = DatasetLoader()
    return _dataset_loader
