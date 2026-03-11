"""
Domain classification — shared between ingestion-service and rag-service.

Ingestion-time:  classify document text → stored in Qdrant metadata
Query-time:      classify query text → Qdrant filter for domain-scoped retrieval

Single source of truth for DOMAIN_LABELS, DOMAIN_DESCRIPTIONS, and the
DeBERTa zero-shot classifier that both services use.
"""

import logging
import os
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DOMAIN_LABELS: list[str] = ["hr_policy", "technical", "contracts", "general"]

DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "hr_policy": "Human resources policies, employee handbooks, leave policies, benefits",
    "technical": "Technical documentation, API references, system architecture, code docs",
    "contracts": "Legal contracts, agreements, terms of service, NDAs",
    "general": "General information, company info, miscellaneous documents",
}

# Default model — override via DOMAIN_CLASSIFIER_MODEL env var
_DEFAULT_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v1.1-all-33"


@dataclass
class ClassificationResult:
    domain: str
    confidence: float
    all_scores: dict[str, float]


class DomainClassifier:
    """
    Lazy-loaded zero-shot domain classifier (DeBERTa).

    Model is loaded on first call to classify() to avoid startup cost.
    Always runs on CPU; GPU is reserved for embedding/LLM workloads.

    Args:
        model_name: HuggingFace model id. Defaults to DOMAIN_CLASSIFIER_MODEL
                    env var, then the bundled DeBERTa default.
    """

    def __init__(self, model_name: str | None = None):
        self.model_name = (
            model_name
            or os.environ.get("DOMAIN_CLASSIFIER_MODEL", _DEFAULT_MODEL)
        )
        self._classifier = None

    def _get_classifier(self):
        if self._classifier is None:
            from transformers import pipeline

            self._classifier = pipeline(
                "zero-shot-classification",
                model=self.model_name,
                device="cpu",
            )
            logger.info("Domain classifier loaded: %s", self.model_name)
        return self._classifier

    def classify(self, text: str, labels: list[str] | None = None) -> ClassificationResult:
        """
        Classify text into one of the domain labels.

        Args:
            text:   Text to classify. Only the first 1000 chars are used for efficiency.
            labels: Custom labels. Defaults to DOMAIN_LABELS.
        """
        if labels is None:
            labels = DOMAIN_LABELS

        text = text[:1000]

        classifier = self._get_classifier()
        result = classifier(text, candidate_labels=labels, multi_label=False)

        all_scores = {
            label: score for label, score in zip(result["labels"], result["scores"])
        }

        return ClassificationResult(
            domain=result["labels"][0],
            confidence=result["scores"][0],
            all_scores=all_scores,
        )


_classifier_instance: Optional[DomainClassifier] = None
_classifier_lock = threading.Lock()


def get_domain_classifier() -> DomainClassifier:
    """Return the process-level singleton DomainClassifier (thread-safe double-checked locking)."""
    global _classifier_instance
    if _classifier_instance is None:
        with _classifier_lock:
            if _classifier_instance is None:
                _classifier_instance = DomainClassifier()
    return _classifier_instance
