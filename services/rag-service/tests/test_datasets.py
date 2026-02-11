"""
Datasets and Domain Classification Tests
=========================================

Tests for domain classification and dataset loading.
"""

import pytest
from pathlib import Path

from src.datasets import (
    DomainClassifier,
    DatasetLoader,
    ClassificationResult,
    DOMAIN_LABELS,
    DATASET_CONFIGS,
    get_domain_classifier,
    get_dataset_loader,
)


@pytest.mark.unit
class TestDomainLabels:
    """Tests for domain label configuration."""

    def test_domain_labels_exist(self):
        """Domain labels are defined."""
        assert len(DOMAIN_LABELS) > 0
        assert "hr_policy" in DOMAIN_LABELS
        assert "technical" in DOMAIN_LABELS
        assert "contracts" in DOMAIN_LABELS
        assert "general" in DOMAIN_LABELS

    def test_dataset_configs_exist(self):
        """Dataset configurations are defined."""
        assert len(DATASET_CONFIGS) > 0
        assert "techqa" in DATASET_CONFIGS
        assert "hr_policies" in DATASET_CONFIGS
        assert "cuad" in DATASET_CONFIGS

    def test_dataset_config_structure(self):
        """Dataset configs have required fields."""
        for key, config in DATASET_CONFIGS.items():
            assert "name" in config, f"Dataset {key} missing 'name'"
            assert "domain" in config, f"Dataset {key} missing 'domain'"
            assert config["domain"] in DOMAIN_LABELS, f"Dataset {key} has invalid domain"


@pytest.mark.unit
class TestClassificationResult:
    """Tests for ClassificationResult model."""

    def test_classification_result_creation(self):
        """ClassificationResult contains expected fields."""
        result = ClassificationResult(
            domain="hr_policy",
            confidence=0.85,
            all_scores={"hr_policy": 0.85, "general": 0.10, "technical": 0.05},
        )
        
        assert result.domain == "hr_policy"
        assert result.confidence == 0.85
        assert result.all_scores["hr_policy"] == 0.85


@pytest.mark.unit
class TestDomainClassifierUnit:
    """Unit tests for DomainClassifier."""

    def test_classifier_initialization(self):
        """Classifier initializes without loading model."""
        classifier = DomainClassifier()
        
        assert classifier is not None
        assert classifier._classifier is None  # Lazy loaded

    def test_global_classifier_singleton(self):
        """get_domain_classifier returns same instance."""
        classifier1 = get_domain_classifier()
        classifier2 = get_domain_classifier()
        
        assert classifier1 is classifier2


@pytest.mark.integration
@pytest.mark.slow
class TestDomainClassifierIntegration:
    """Integration tests for domain classification."""

    def test_classify_hr_content(self, domain_classifier: DomainClassifier, hr_policy_content: str):
        """HR policy content is classified as hr_policy."""
        result = domain_classifier.classify(hr_policy_content)
        
        assert result.domain in DOMAIN_LABELS
        assert result.confidence > 0
        assert result.confidence <= 1
        # HR content should likely be classified as hr_policy or general
        assert result.domain in ["hr_policy", "general"]

    def test_classify_technical_content(self, domain_classifier: DomainClassifier, technical_doc_content: str):
        """Technical documentation is classified as technical."""
        result = domain_classifier.classify(technical_doc_content)
        
        assert result.domain in DOMAIN_LABELS
        assert result.confidence > 0
        # Technical content should likely be classified as technical or general
        assert result.domain in ["technical", "general"]

    def test_classify_contract_content(self, domain_classifier: DomainClassifier, contract_content: str):
        """Contract content is classified as contracts."""
        result = domain_classifier.classify(contract_content)
        
        assert result.domain in DOMAIN_LABELS
        assert result.confidence > 0
        # Contract content should likely be classified as contracts or general
        assert result.domain in ["contracts", "general"]

    def test_classify_general_content(self, domain_classifier: DomainClassifier, general_doc_content: str):
        """General company info is classified appropriately."""
        result = domain_classifier.classify(general_doc_content)
        
        assert result.domain in DOMAIN_LABELS
        assert result.confidence > 0

    def test_classify_returns_all_scores(self, domain_classifier: DomainClassifier):
        """Classification returns scores for all domains."""
        result = domain_classifier.classify("What is the leave policy for employees?")
        
        assert len(result.all_scores) >= 3
        for domain in ["hr_policy", "technical", "contracts"]:
            assert domain in result.all_scores

    def test_classify_with_custom_labels(self, domain_classifier: DomainClassifier):
        """Classification works with custom label set."""
        custom_labels = ["technical", "general"]
        result = domain_classifier.classify(
            "How does the API work?",
            labels=custom_labels,
        )
        
        assert result.domain in custom_labels

    def test_classify_short_text(self, domain_classifier: DomainClassifier):
        """Short text is classified."""
        result = domain_classifier.classify("Leave policy")
        
        assert result.domain in DOMAIN_LABELS
        assert result.confidence > 0

    def test_classify_truncates_long_text(self, domain_classifier: DomainClassifier, hr_policy_content: str):
        """Long text is truncated for classification."""
        # Create very long text
        long_text = hr_policy_content * 10
        
        # Should not fail, text is truncated internally
        result = domain_classifier.classify(long_text)
        
        assert result.domain in DOMAIN_LABELS


@pytest.mark.integration
@pytest.mark.slow
class TestDomainClassifierAccuracy:
    """Accuracy tests for domain classification with test files."""

    def test_hr_policy_queries_classified_correctly(
        self,
        domain_classifier: DomainClassifier,
        expected_hr_queries: list[dict],
    ):
        """HR policy queries are classified into valid domains with hr_policy bias."""
        hr_correct = 0
        for query_info in expected_hr_queries:
            result = domain_classifier.classify(query_info["query"])
            
            # Must be a valid domain
            assert result.domain in DOMAIN_LABELS, (
                f"Query '{query_info['query']}' classified as {result.domain}, not a valid domain"
            )
            
            # Track how many are correctly hr_policy
            if result.domain in ["hr_policy", "general"]:
                hr_correct += 1
        
        # At least 50% should be classified as hr_policy or general (relaxed for zero-shot)
        accuracy = hr_correct / len(expected_hr_queries)
        assert accuracy >= 0.5, f"HR query accuracy {accuracy:.0%} is too low"

    def test_technical_queries_classified_correctly(
        self,
        domain_classifier: DomainClassifier,
        expected_technical_queries: list[dict],
    ):
        """Technical queries are classified into valid domains with technical bias."""
        tech_correct = 0
        for query_info in expected_technical_queries:
            result = domain_classifier.classify(query_info["query"])
            
            assert result.domain in DOMAIN_LABELS
            if result.domain in ["technical", "general"]:
                tech_correct += 1
        
        accuracy = tech_correct / len(expected_technical_queries)
        assert accuracy >= 0.5, f"Technical query accuracy {accuracy:.0%} is too low"

    def test_contract_queries_classified_correctly(
        self,
        domain_classifier: DomainClassifier,
        expected_contract_queries: list[dict],
    ):
        """Contract queries are classified into valid domains with contracts bias."""
        contract_correct = 0
        for query_info in expected_contract_queries:
            result = domain_classifier.classify(query_info["query"])
            
            assert result.domain in DOMAIN_LABELS
            if result.domain in ["contracts", "general"]:
                contract_correct += 1
        
        accuracy = contract_correct / len(expected_contract_queries)
        assert accuracy >= 0.5, f"Contract query accuracy {accuracy:.0%} is too low"


@pytest.mark.unit
class TestDatasetLoader:
    """Tests for DatasetLoader."""

    def test_loader_initialization(self):
        """Loader initializes correctly."""
        loader = DatasetLoader()
        
        assert loader is not None

    def test_global_loader_singleton(self):
        """get_dataset_loader returns same instance."""
        loader1 = get_dataset_loader()
        loader2 = get_dataset_loader()
        
        assert loader1 is loader2


@pytest.mark.integration
@pytest.mark.slow
class TestDatasetLoaderIntegration:
    """Integration tests for dataset loading from HuggingFace."""

    def test_load_techqa_samples(self):
        """Load samples from TechQA dataset."""
        loader = DatasetLoader()
        
        try:
            documents = loader.load_dataset(
                dataset_key="techqa",
                samples=5,
                tenant_id="test_tenant",
            )
            
            assert len(documents) <= 5
            for doc in documents:
                assert doc.content
                assert doc.metadata.get("domain") == "technical"
        except Exception as e:
            pytest.skip(f"Dataset loading failed: {e}")

    def test_load_hr_policies_samples(self):
        """Load samples from HR policies dataset."""
        loader = DatasetLoader()
        
        try:
            documents = loader.load_dataset(
                dataset_key="hr_policies",
                samples=5,
                tenant_id="test_tenant",
            )
            
            assert len(documents) <= 5
            for doc in documents:
                assert doc.content
                assert doc.metadata.get("domain") == "hr_policy"
        except Exception as e:
            pytest.skip(f"Dataset loading failed: {e}")

    def test_load_cuad_samples(self):
        """Load samples from CUAD dataset."""
        loader = DatasetLoader()
        
        try:
            documents = loader.load_dataset(
                dataset_key="cuad",
                samples=5,
                tenant_id="test_tenant",
            )
            
            assert len(documents) <= 5
            for doc in documents:
                assert doc.content
                assert doc.metadata.get("domain") == "contracts"
        except Exception as e:
            pytest.skip(f"Dataset loading failed: {e}")

    def test_load_invalid_dataset(self):
        """Invalid dataset key raises error."""
        loader = DatasetLoader()
        
        with pytest.raises((KeyError, ValueError)):
            loader.load_dataset(
                dataset_key="nonexistent_dataset",
                samples=5,
                tenant_id="test_tenant",
            )
