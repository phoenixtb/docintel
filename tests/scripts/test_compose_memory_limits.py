"""
Static-parse tests: every long-running service has a memory limit, reservations
stay below limits (also with the GPU overlay), and JVM heaps follow the limit.

Without limits, one service's spike could take the host's memory and get LMForge
killed (16 GB Ubuntu box) or overflow the Docker VM (Mac). Docker refuses a
reservation above a limit at `up` time.
"""

import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent.parent
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]
GPU = yaml.safe_load((REPO_ROOT / "docker-compose.gpu.yml").read_text())["services"]

ONE_SHOT = {"object-store-init"}
JVM_SERVICES = ["api-gateway", "document-service", "admin-service"]
_UNITS = {"k": 1 << 10, "m": 1 << 20, "g": 1 << 30}


def _bytes(value: str) -> int:
    """'1536M', '1G' or '${VAR:-3G}' (its default) → bytes."""
    m = re.fullmatch(r"\$\{[A-Z_]+:-([^}]+)\}", str(value))
    raw = (m.group(1) if m else str(value)).strip().lower().rstrip("b")
    return int(float(raw[:-1]) * _UNITS[raw[-1]]) if raw[-1] in _UNITS else int(raw)


def _memory(service: dict, kind: str) -> str | None:
    return ((service.get("deploy") or {}).get("resources") or {}).get(kind, {}).get("memory")


def test_every_long_running_service_has_a_memory_limit():
    missing = [n for n, s in COMPOSE.items() if n not in ONE_SHOT and not _memory(s, "limits")]
    assert missing == []


def test_reservations_never_exceed_limits():
    for name, service in COMPOSE.items():
        limit = _memory(service, "limits")
        for reservation in (_memory(service, "reservations"), _memory(GPU.get(name, {}), "reservations")):
            if limit and reservation:
                assert _bytes(reservation) <= _bytes(limit), name


def test_jvm_heaps_follow_the_container_limit():
    for name in JVM_SERVICES:
        env = COMPOSE[name]["environment"]
        assert any(e.startswith("JAVA_TOOL_OPTIONS=") and "MaxRAMPercentage" in e for e in env), name


def test_limit_parsing_reads_interpolation_defaults():
    assert _bytes("${INGESTION_MEMORY_LIMIT:-3G}") == 3 << 30
    assert _bytes("1536M") == 1536 << 20
