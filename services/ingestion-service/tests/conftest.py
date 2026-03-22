"""
Shared pytest fixtures for ingestion-service tests.
"""

import os

import pytest

# Disable Redis stream consumer in tests — avoids needing a live Redis instance.
os.environ.setdefault("STREAM_CONSUMER_ENABLED", "false")

# macOS ships two OpenMP runtimes (Apple Accelerate + conda/PyTorch) that clash.
# This env var is harmless on Linux containers and essential on macOS dev machines.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Prevent auth check from failing due to missing secret in tests.
os.environ.setdefault("INTERNAL_GATEWAY_SECRET", "test-secret-for-unit-tests")

from src.job_registry import JobRegistry


@pytest.fixture
def registry() -> JobRegistry:
    """Fresh JobRegistry for each test."""
    return JobRegistry()
