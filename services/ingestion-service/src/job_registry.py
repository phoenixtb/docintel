"""
In-memory job registry for dataset ingestion SSE progress streaming.

Isolation model:
  - job_id is a UUID4 (122 bits of entropy — unguessable).
  - The SSE subscribe endpoint validates X-Tenant-Id (gateway-injected from JWT)
    against the job's tenant_id, so cross-tenant reads are impossible even if
    a job_id leaks.
  - Multiple subscribers (e.g. two tabs same user) both receive events because
    we fan-out via a broadcast list rather than a consumed queue.
  - Jobs are auto-evicted after TTL seconds post-completion to avoid memory leak.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)

_TTL_AFTER_DONE = 300   # seconds to keep a completed job in memory
_MAX_EVENTS_KEPT = 2000  # per job cap — prevents unbounded list growth


@dataclass
class _Job:
    job_id: str
    tenant_id: str
    datasets: list[str]
    total_files: int          # 0 = unknown until fetching completes
    processed_files: int = 0
    done: bool = False
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    # Replay buffer — all events ever emitted (new subscribers replay from pos 0)
    _events: list[dict] = field(default_factory=list)
    # Per-subscriber notification: each asyncio.Event is set when a new event arrives
    _waiters: list[asyncio.Event] = field(default_factory=list)

    def push(self, event_type: str, data: dict) -> None:
        if len(self._events) < _MAX_EVENTS_KEPT:
            self._events.append({"type": event_type, "data": data})
        for w in self._waiters:
            w.set()

    def snapshot_from(self, pos: int) -> tuple[list[dict], int]:
        """Return events[pos:] and new position."""
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
    # Writer API (called by ingestion background task)
    # ------------------------------------------------------------------

    def create(self, tenant_id: str, datasets: list[str]) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = _Job(
            job_id=job_id,
            tenant_id=tenant_id,
            datasets=datasets,
            total_files=0,
        )
        logger.info("Job %s created for tenant=%s datasets=%s", job_id, tenant_id, datasets)
        return job_id

    def set_total(self, job_id: str, total: int) -> None:
        job = self._jobs.get(job_id)
        if job:
            job.total_files = total
            job.push("total", {"total": total})

    def file_done(self, job_id: str, filename: str, domain: str, chunk_count: int) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.processed_files += 1
        job.push("progress", {
            "processed": job.processed_files,
            "total": job.total_files,
            "filename": filename,
            "domain": domain,
            "chunk_count": chunk_count,
        })

    def finish(self, job_id: str, total_chunks: int) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.done = True
        job.finished_at = time.monotonic()
        job.push("done", {"total_chunks": total_chunks, "processed": job.processed_files})
        logger.info("Job %s finished: %d files, %d chunks", job_id, job.processed_files, total_chunks)

    def fail(self, job_id: str, reason: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return
        job.done = True
        job.error = reason
        job.finished_at = time.monotonic()
        job.push("error", {"reason": reason})

    # ------------------------------------------------------------------
    # Reader API (called by SSE endpoint)
    # ------------------------------------------------------------------

    def get_validated(self, job_id: str, tenant_id: str) -> _Job | None:
        """Return job only if it belongs to the requesting tenant."""
        job = self._jobs.get(job_id)
        if job is None or job.tenant_id != tenant_id:
            return None
        return job

    async def stream(self, job_id: str, tenant_id: str) -> AsyncIterator[str]:
        """
        Async generator that yields SSE-formatted lines.

        Replays already-emitted events first, then waits for new ones.
        Safe for multiple concurrent subscribers (fan-out via asyncio.Event).
        Exits when the job is marked done and all events are delivered.
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

                # Wait for the next push (or timeout after 25s to send a keepalive).
                # asyncio.Event.wait() is cancel-safe — no shield/ensure_future needed.
                waiter.clear()
                try:
                    await asyncio.wait_for(waiter.wait(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # SSE comment — keeps connection alive through proxies
        finally:
            job.remove_waiter(waiter)

    # ------------------------------------------------------------------
    # Eviction (call periodically)
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


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _sse(event_type: str, data: dict) -> str:
    import json
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

