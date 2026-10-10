"""
RAG Service Test Configuration
==============================

Shared fixtures and configuration for all tests.
"""

import os

# Fix OpenMP library conflict that occurs with haystack/torch/numpy on macOS
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import pytest
from pathlib import Path
from typing import Generator
import uuid

# ---------------------------------------------------------------------------
# Load model defaults from config/defaults.env — single source of truth.
# This keeps tests aligned with scripts and production config.
# ---------------------------------------------------------------------------
def _load_defaults_env() -> dict[str, str]:
    """Parse config/defaults.env as key=value pairs (no shell quoting)."""
    defaults_path = Path(__file__).parents[3] / "config" / "defaults.env"
    result: dict[str, str] = {}
    if defaults_path.exists():
        for line in defaults_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    return result

_DEFAULTS = _load_defaults_env()
_DEFAULT_LLM_MODEL = _DEFAULTS.get("DEFAULT_LLM_MODEL", "qwen3.5:4b")
_DEFAULT_EMBED_MODEL = _DEFAULTS.get("DEFAULT_EMBED_MODEL", "nomic-embed-text")

# Set test environment before imports
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
os.environ.setdefault("LLM_CHAT_URL", "http://localhost:11434/v1")
os.environ.setdefault("LLM_EMBED_URL", "http://localhost:7997/v1")
os.environ.setdefault("LLM_MODEL", _DEFAULT_LLM_MODEL)
os.environ.setdefault("LLM_EMBED_MODEL", _DEFAULT_EMBED_MODEL)
os.environ.setdefault("LLM_API_KEY", "none")


# =============================================================================
# Path Fixtures
# =============================================================================

RESOURCES_DIR = Path(__file__).parent / "resources"


@pytest.fixture
def resources_dir() -> Path:
    """Path to test resources directory."""
    return RESOURCES_DIR


@pytest.fixture
def hr_policy_file(resources_dir: Path) -> Path:
    """Path to HR policy test document."""
    return resources_dir / "hr_policy_leave.txt"


@pytest.fixture
def technical_doc_file(resources_dir: Path) -> Path:
    """Path to technical documentation test document."""
    return resources_dir / "technical_api_docs.txt"


@pytest.fixture
def contract_file(resources_dir: Path) -> Path:
    """Path to contract test document."""
    return resources_dir / "contract_saas_agreement.txt"


@pytest.fixture
def general_doc_file(resources_dir: Path) -> Path:
    """Path to general company info test document."""
    return resources_dir / "general_company_about.txt"


# =============================================================================
# Document Content Fixtures
# =============================================================================

@pytest.fixture
def hr_policy_content(hr_policy_file: Path) -> str:
    """Load HR policy document content."""
    return hr_policy_file.read_text()


@pytest.fixture
def technical_doc_content(technical_doc_file: Path) -> str:
    """Load technical documentation content."""
    return technical_doc_file.read_text()


@pytest.fixture
def contract_content(contract_file: Path) -> str:
    """Load contract document content."""
    return contract_file.read_text()


@pytest.fixture
def general_doc_content(general_doc_file: Path) -> str:
    """Load general company info content."""
    return general_doc_file.read_text()


@pytest.fixture
def all_documents(
    hr_policy_content: str,
    technical_doc_content: str,
    contract_content: str,
    general_doc_content: str,
) -> dict[str, dict]:
    """All test documents with metadata."""
    return {
        "hr_policy": {
            "content": hr_policy_content,
            "filename": "hr_policy_leave.txt",
            "domain": "hr_policy",
            "document_id": str(uuid.uuid4()),
        },
        "technical": {
            "content": technical_doc_content,
            "filename": "technical_api_docs.txt",
            "domain": "technical",
            "document_id": str(uuid.uuid4()),
        },
        "contract": {
            "content": contract_content,
            "filename": "contract_saas_agreement.txt",
            "domain": "contracts",
            "document_id": str(uuid.uuid4()),
        },
        "general": {
            "content": general_doc_content,
            "filename": "general_company_about.txt",
            "domain": "general",
            "document_id": str(uuid.uuid4()),
        },
    }


# =============================================================================
# Test Tenant Fixtures
# =============================================================================

@pytest.fixture
def test_tenant_id() -> str:
    """Generate unique tenant ID for test isolation."""
    return f"test_tenant_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def test_document_id() -> str:
    """Generate unique document ID."""
    return str(uuid.uuid4())


# =============================================================================
# Qdrant Fixtures
# =============================================================================

# Keep in sync with the qdrant service in docker-compose.yml
# (enforced by scripts/check-image-pins.sh).
QDRANT_IMAGE = "qdrant/qdrant:v1.16.3"


@pytest.fixture(scope="session")
def qdrant_url() -> Generator[str, None, None]:
    """A real Qdrant started by Testcontainers for the test session.

    Requires Docker. There is deliberately no skip when Docker is missing:
    integration tests that silently skip look green in CI while testing nothing.
    """
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import HttpWaitStrategy

    container = (
        DockerContainer(QDRANT_IMAGE)
        .with_exposed_ports(6333)
        .waiting_for(HttpWaitStrategy(6333, "/readyz").for_status_code(200))
    )
    with container:
        yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(6333)}"


@pytest.fixture
def qdrant_settings(qdrant_url: str):
    """Settings pointing at the test Qdrant; resets the per-tenant retriever cache.

    SecureRetriever caches retrievers per (tenant, top_k) at module level, so a
    retriever built by another test against a different store must not leak in.
    """
    from src.components import retrieval
    from src.config import get_settings

    retrieval._retriever_cache.clear()
    yield get_settings().model_copy(update={"qdrant_url": qdrant_url})
    retrieval._retriever_cache.clear()


# =============================================================================
# Service Fixtures
# =============================================================================

# =============================================================================
# API Client Fixtures
# =============================================================================

@pytest.fixture
def api_client():
    """Create FastAPI test client."""
    from fastapi.testclient import TestClient
    from src.api.main import app
    return TestClient(app)


# =============================================================================
# Expected Outcomes
# =============================================================================

@pytest.fixture
def expected_hr_queries() -> list[dict]:
    """Expected queries and outcomes for HR policy document."""
    return [
        {
            "query": "How many days of annual leave do employees get?",
            "expected_keywords": ["25 days", "annual leave", "full-time"],
            "expected_domain": "hr_policy",
        },
        {
            "query": "What is the process for requesting sick leave?",
            "expected_keywords": ["notify", "manager", "9:00 AM", "medical certificate"],
            "expected_domain": "hr_policy",
        },
        {
            "query": "How long is maternity leave?",
            "expected_keywords": ["16 weeks", "maternity", "primary caregiver"],
            "expected_domain": "hr_policy",
        },
        {
            "query": "Can I carry forward unused vacation days?",
            "expected_keywords": ["5", "carry forward", "March 31"],
            "expected_domain": "hr_policy",
        },
    ]


@pytest.fixture
def expected_technical_queries() -> list[dict]:
    """Expected queries and outcomes for technical documentation."""
    return [
        {
            "query": "What is the rate limit for the API?",
            "expected_keywords": ["100 requests", "1000", "per minute"],
            "expected_domain": "technical",
        },
        {
            "query": "How do I authenticate API requests?",
            "expected_keywords": ["Bearer token", "Authorization", "API key"],
            "expected_domain": "technical",
        },
        {
            "query": "What file formats are supported for document upload?",
            "expected_keywords": ["PDF", "DOCX", "TXT"],
            "expected_domain": "technical",
        },
        {
            "query": "How does the streaming query endpoint work?",
            "expected_keywords": ["SSE", "Server-Sent Events", "stream", "token"],
            "expected_domain": "technical",
        },
    ]


@pytest.fixture
def expected_contract_queries() -> list[dict]:
    """Expected queries and outcomes for contract document."""
    return [
        {
            "query": "What is the termination notice period?",
            "expected_keywords": ["60 days", "written notice", "non-renewal"],
            "expected_domain": "contracts",
        },
        {
            "query": "What security certifications does the provider have?",
            "expected_keywords": ["SOC 2", "encryption", "AES-256", "multi-factor"],
            "expected_domain": "contracts",
        },
        {
            "query": "What is the limitation of liability?",
            "expected_keywords": ["12 months", "fees paid", "indirect", "consequential"],
            "expected_domain": "contracts",
        },
        {
            "query": "How long is customer data retained after termination?",
            "expected_keywords": ["30 days", "permanently deleted", "termination"],
            "expected_domain": "contracts",
        },
    ]


@pytest.fixture
def expected_general_queries() -> list[dict]:
    """Expected queries and outcomes for general company document."""
    return [
        {
            "query": "When was the company founded?",
            "expected_keywords": ["2018", "Austin", "Texas"],
            "expected_domain": "general",
        },
        {
            "query": "Who is the CEO of the company?",
            "expected_keywords": ["Sarah Chen", "CEO", "Chief Executive"],
            "expected_domain": "general",
        },
        {
            "query": "What products does the company offer?",
            "expected_keywords": ["DocIntel", "DataFlow", "AutomateX", "SecureVault"],
            "expected_domain": "general",
        },
        {
            "query": "How many employees does the company have?",
            "expected_keywords": ["850", "employees"],
            "expected_domain": "general",
        },
    ]


# =============================================================================
# Markers
# =============================================================================

def pytest_configure(config):
    """Configure custom markers."""
    config.addinivalue_line("markers", "slow: mark test as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "unit: mark test as unit test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
