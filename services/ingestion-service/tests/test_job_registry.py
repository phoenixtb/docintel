"""
Unit tests for JobRegistry.

All behaviour tested here is pure in-memory logic — no external dependencies.
Async tests work because asyncio_mode = "auto" is set in pyproject.toml.
"""

import time
import uuid

import pytest

from src.job_registry import JobRegistry, _TTL_AFTER_DONE


# ---------------------------------------------------------------------------
# Writer API — synchronous
# ---------------------------------------------------------------------------


def test_create_returns_uuid_and_stores_job(registry: JobRegistry) -> None:
    job_id = registry.create("tenant-a", ["cuad"])
    # Must be a valid UUID4
    parsed = uuid.UUID(job_id, version=4)
    assert str(parsed) == job_id
    # Must be retrievable
    job = registry.get_validated(job_id, "tenant-a")
    assert job is not None
    assert job.tenant_id == "tenant-a"
    assert job.datasets == ["cuad"]
    assert job.done is False


def test_set_total_pushes_total_event(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["techqa"])
    registry.set_total(job_id, 50)
    job = registry._jobs[job_id]
    assert job.total_files == 50
    assert any(e["type"] == "total" and e["data"]["total"] == 50 for e in job._events)


def test_file_done_increments_counter_and_pushes_event(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["techqa"])
    registry.set_total(job_id, 5)
    registry.file_done(job_id, "doc1.txt", "technical", 42)
    job = registry._jobs[job_id]
    assert job.processed_files == 1
    progress_events = [e for e in job._events if e["type"] == "progress"]
    assert len(progress_events) == 1
    assert progress_events[0]["data"]["processed"] == 1
    assert progress_events[0]["data"]["filename"] == "doc1.txt"
    assert progress_events[0]["data"]["chunk_count"] == 42


def test_file_done_accumulates_across_multiple_calls(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["cuad"])
    registry.set_total(job_id, 3)
    registry.file_done(job_id, "a.txt", "contracts", 5)
    registry.file_done(job_id, "b.txt", "contracts", 7)
    registry.file_done(job_id, "c.txt", "contracts", 3)
    assert registry._jobs[job_id].processed_files == 3


def test_finish_marks_done_and_pushes_done_event(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["cuad"])
    registry.set_total(job_id, 1)
    registry.file_done(job_id, "a.txt", "contracts", 10)
    registry.finish(job_id, 10)
    job = registry._jobs[job_id]
    assert job.done is True
    assert job.finished_at is not None
    assert job.error is None
    done_events = [e for e in job._events if e["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["data"]["total_chunks"] == 10
    assert done_events[0]["data"]["processed"] == 1


def test_fail_marks_done_with_error(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["cuad"])
    registry.fail(job_id, "Ollama died")
    job = registry._jobs[job_id]
    assert job.done is True
    assert job.error == "Ollama died"
    assert job.finished_at is not None
    error_events = [e for e in job._events if e["type"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["data"]["reason"] == "Ollama died"


# ---------------------------------------------------------------------------
# Reader API — get_validated
# ---------------------------------------------------------------------------


def test_get_validated_correct_tenant_returns_job(registry: JobRegistry) -> None:
    job_id = registry.create("tenant-x", ["hr_policies"])
    result = registry.get_validated(job_id, "tenant-x")
    assert result is not None
    assert result.job_id == job_id


def test_get_validated_wrong_tenant_returns_none(registry: JobRegistry) -> None:
    job_id = registry.create("tenant-a", ["cuad"])
    result = registry.get_validated(job_id, "tenant-b")
    assert result is None


def test_get_validated_unknown_job_id_returns_none(registry: JobRegistry) -> None:
    result = registry.get_validated(str(uuid.uuid4()), "tenant-a")
    assert result is None


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------


def test_evict_expired_removes_old_completed_jobs(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["cuad"])
    registry.finish(job_id, 5)
    # Back-date finished_at beyond TTL
    registry._jobs[job_id].finished_at = time.monotonic() - _TTL_AFTER_DONE - 1

    registry.evict_expired()

    assert job_id not in registry._jobs


def test_evict_does_not_remove_recently_completed_jobs(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["cuad"])
    registry.finish(job_id, 5)
    # finished_at is "just now" — within TTL

    registry.evict_expired()

    assert job_id in registry._jobs


def test_evict_does_not_remove_in_progress_jobs(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["cuad"])
    # Back-date created_at heavily — but job is NOT done
    registry._jobs[job_id].created_at = time.monotonic() - 99999

    registry.evict_expired()

    assert job_id in registry._jobs


# ---------------------------------------------------------------------------
# Reader API — stream (async)
# ---------------------------------------------------------------------------


async def test_stream_replays_all_emitted_events(registry: JobRegistry) -> None:
    job_id = registry.create("t1", ["cuad"])
    registry.set_total(job_id, 2)
    registry.file_done(job_id, "a.txt", "contracts", 10)
    registry.file_done(job_id, "b.txt", "contracts", 20)
    registry.finish(job_id, 30)

    chunks: list[str] = []
    async for chunk in registry.stream(job_id, "t1"):
        chunks.append(chunk)

    joined = "".join(chunks)
    assert "total" in joined
    assert "progress" in joined
    assert "done" in joined
    # Both filenames must appear
    assert "a.txt" in joined
    assert "b.txt" in joined


async def test_stream_unknown_job_yields_error_event(registry: JobRegistry) -> None:
    bad_id = str(uuid.uuid4())
    chunks: list[str] = []
    async for chunk in registry.stream(bad_id, "t1"):
        chunks.append(chunk)

    joined = "".join(chunks)
    assert "error" in joined
    assert "job not found" in joined


async def test_stream_tenant_isolation_yields_error(registry: JobRegistry) -> None:
    job_id = registry.create("tenant-owner", ["cuad"])
    registry.finish(job_id, 0)

    chunks: list[str] = []
    async for chunk in registry.stream(job_id, "tenant-other"):
        chunks.append(chunk)

    joined = "".join(chunks)
    assert "error" in joined
