"""
In-memory job registry for dataset load SSE progress streaming.

Isolation model:
  - job_id is a UUID4 (122 bits of entropy — unguessable).
  - The SSE subscribe endpoint validates X-Tenant-Id (gateway-injected from JWT)
    against the job's tenant_id, so cross-tenant reads are impossible even if
    a job_id leaks.
  - Multiple subscribers receive the same events (fan-out via broadcast list).
  - Jobs are auto-evicted after TTL seconds post-completion.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_TTL_AFTER_DONE = 300    # seconds to keep a completed job in memory
_MAX_EVENTS_KEPT = 2000  # per-job cap


@dataclass
class _Job:
    job_id: str
    tenant_id: str
    datasets: list[str]
    total_files: int = 0
    processed_files: int = 0
    done: bool = False
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    _events: list[dict] = field(default_factory=list)
    _waiters: list[asyncio.Event] = field(default_factory=list)

    def push(self, event_type: str, data: dict) -> None:
        if len(self._events) < _MAX_EVENTS_KEPT:
            self._events.append({"type": event_type, "data": data})
        for w in self._waiters:
            w.set()

    def snapshot_from(self, pos: int) -> tuple[list[dict], int]:
        events = self._events[pos:]
        return events, len(self._events)

    def add_waiter(self) -> asyncio.Event:
        ev = asyncio.Event()
        self._waiters.append(ev)
        return ev

    def remove_waiter(self, ev: asyncio.Event) -> None:
        try:
            self._waiters.remove(ev)
        except ValueError:
            pass


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}

    # ------------------------------------------------------------------
    # Writer API
    # ------------------------------------------------------------------

    def create(self, tenant_id: str, datasets: list[str]) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = _Job(
            job_id=job_id,
            tenant_id=tenant_id,
            datasets=datasets,
        )
        logger.info("Job %s created for tenant=%s datasets=%s", job_id, tenant_id, datasets)
        return job_id

    def set_total(self, job_id: str, total: int) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.total_files = total
            job.push("total", {"total": total})

    def file_done(self, job_id: str, filename: str, domain: str, deduplicated: bool = False) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.processed_files += 1
        job.push("progress", {
            "processed": job.processed_files,
            "total": job.total_files,
            "filename": filename,
            "domain": domain,
            "deduplicated": deduplicated,
        })

    def finish(self, job_id: str, registered: int, deduplicated: int) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.done = True
        job.finished_at = time.monotonic()
        job.push("done", {
            "processed": job.processed_files,
            "registered": registered,
            "deduplicated": deduplicated,
        })
        logger.info(
            "Job %s finished: %d files, %d registered, %d deduplicated",
            job_id, job.processed_files, registered, deduplicated,
        )

    def fail(self, job_id: str, reason: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.done = True
        job.error = reason
        job.finished_at = time.monotonic()
        job.push("error", {"reason": reason})

    # ------------------------------------------------------------------
    # Reader API
    # ------------------------------------------------------------------

    def get_validated(self, job_id: str, tenant_id: str) -> _Job | None:
        job = self._jobs.get(job_id)
        if job is None or job.tenant_id != tenant_id:
            return None
        return job

    async def stream(self, job_id: str, tenant_id: str) -> AsyncIterator[str]:
        """
        Async generator that yields SSE-formatted lines.
        Replays existing events first, then waits for new ones.
        """
        job = self.get_validated(job_id, tenant_id)
        if job is None:
            yield _sse("error", {"reason": "job not found or access denied"})
            return

        pos = 0
        waiter = job.add_waiter()
        try:
            while True:
                events, pos = job.snapshot_from(pos)
                for ev in events:
                    yield _sse(ev["type"], ev["data"])

                if job.done and not job._events[pos:]:
                    break

                waiter.clear()
                try:
                    await asyncio.wait_for(waiter.wait(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            job.remove_waiter(waiter)

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def evict_expired(self) -> None:
        now = time.monotonic()
        expired = [
            jid for jid, j in self._jobs.items()
            if j.done and j.finished_at is not None and now - j.finished_at > _TTL_AFTER_DONE
        ]
        for jid in expired:
            del self._jobs[jid]
        if expired:
            logger.debug("Evicted %d completed jobs", len(expired))


def _sse(event_type: str, data: dict) -> str:
    import json
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
