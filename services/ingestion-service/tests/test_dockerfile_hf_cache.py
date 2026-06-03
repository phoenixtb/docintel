"""
Static-parse smoke tests for the HF_HOME / volume-mount changes.

No Docker daemon is required — we just read the Dockerfile and docker-compose.yml
text and assert the right patterns appear in the right order.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
INGESTION_DOCKERFILE = REPO_ROOT / "services" / "ingestion-service" / "Dockerfile"
RAG_DOCKERFILE = REPO_ROOT / "services" / "rag-service" / "Dockerfile"
DATA_LOADER_DOCKERFILE = REPO_ROOT / "services" / "data-loader" / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"


# ---------------------------------------------------------------------------
# Dockerfile assertions
# ---------------------------------------------------------------------------


def _dockerfile_lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _find_line_index(lines: list[str], fragment: str) -> int:
    for i, line in enumerate(lines):
        if fragment in line:
            return i
    return -1


def test_ingestion_dockerfile_sets_hf_home() -> None:
    lines = _dockerfile_lines(INGESTION_DOCKERFILE)
    assert _find_line_index(lines, "ENV HF_HOME=/opt/hf-cache") >= 0, (
        "ingestion-service Dockerfile missing: ENV HF_HOME=/opt/hf-cache"
    )


def test_ingestion_dockerfile_bake_after_env_hf_home() -> None:
    lines = _dockerfile_lines(INGESTION_DOCKERFILE)
    env_idx = _find_line_index(lines, "ENV HF_HOME=/opt/hf-cache")
    bake_idx = _find_line_index(lines, "AutoTokenizer.from_pretrained('bert-base-uncased')")
    assert env_idx >= 0 and bake_idx >= 0, "Dockerfile missing ENV or bake step"
    assert bake_idx > env_idx, (
        f"Bake step (line {bake_idx}) must come AFTER ENV HF_HOME (line {env_idx})"
    )


def test_ingestion_dockerfile_chmod_after_bake() -> None:
    """chmod -R 0777 must follow the bake step so hub/ subdirs are world-writable at runtime."""
    lines = _dockerfile_lines(INGESTION_DOCKERFILE)
    bake_idx = _find_line_index(lines, "AutoTokenizer.from_pretrained('bert-base-uncased')")
    chmod_idx = _find_line_index(lines, "chmod -R 0777 /opt/hf-cache")
    assert bake_idx >= 0 and chmod_idx >= 0, "Dockerfile missing bake step or chmod"
    assert chmod_idx > bake_idx, (
        f"chmod (line {chmod_idx}) must come AFTER the bake step (line {bake_idx})"
    )


def test_rag_dockerfile_sets_hf_home() -> None:
    lines = _dockerfile_lines(RAG_DOCKERFILE)
    assert _find_line_index(lines, "ENV HF_HOME=/opt/hf-cache") >= 0, (
        "rag-service Dockerfile missing: ENV HF_HOME=/opt/hf-cache"
    )


def test_data_loader_dockerfile_sets_hf_home() -> None:
    lines = _dockerfile_lines(DATA_LOADER_DOCKERFILE)
    assert _find_line_index(lines, "ENV HF_HOME=/opt/hf-cache") >= 0, (
        "data-loader Dockerfile missing: ENV HF_HOME=/opt/hf-cache"
    )


# ---------------------------------------------------------------------------
# docker-compose.yml assertions
# ---------------------------------------------------------------------------


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text())


def _service_volume_mounts(compose: dict, service_name: str) -> list[str]:
    return compose.get("services", {}).get(service_name, {}).get("volumes", [])


def test_ingestion_service_mounts_hf_cache_at_opt() -> None:
    compose = _load_compose()
    mounts = _service_volume_mounts(compose, "ingestion-service")
    assert any("/opt/hf-cache" in m for m in mounts), (
        f"ingestion-service volumes do not mount huggingface-cache at /opt/hf-cache: {mounts}"
    )


def test_rag_service_mounts_hf_cache_at_opt() -> None:
    compose = _load_compose()
    mounts = _service_volume_mounts(compose, "rag-service")
    assert any("/opt/hf-cache" in m for m in mounts), (
        f"rag-service volumes do not mount huggingface-cache at /opt/hf-cache: {mounts}"
    )


def test_infinity_mount_unchanged() -> None:
    """infinity is a 3rd-party image that runs as root; its mount must stay at the original path."""
    compose = _load_compose()
    mounts = _service_volume_mounts(compose, "infinity")
    assert any("/root/.cache/huggingface" in m for m in mounts), (
        f"infinity volume mount changed unexpectedly: {mounts}"
    )
    assert not any("/opt/hf-cache" in m for m in mounts), (
        "infinity must NOT use /opt/hf-cache (external image, runs as root)"
    )


def test_ingestion_does_not_mount_at_root_cache() -> None:
    compose = _load_compose()
    mounts = _service_volume_mounts(compose, "ingestion-service")
    assert not any("/root/.cache/huggingface" in m for m in mounts), (
        "ingestion-service still mounts at /root/.cache/huggingface — should be /opt/hf-cache"
    )


def test_rag_does_not_mount_at_root_cache() -> None:
    compose = _load_compose()
    mounts = _service_volume_mounts(compose, "rag-service")
    assert not any("/root/.cache/huggingface" in m for m in mounts), (
        "rag-service still mounts at /root/.cache/huggingface — should be /opt/hf-cache"
    )
