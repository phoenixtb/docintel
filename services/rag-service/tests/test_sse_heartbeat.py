"""
tests/test_sse_heartbeat.py — regression test for _with_heartbeat().

_with_heartbeat wraps an async generator to inject keepalive sentinels during
idle gaps (see docs/api/sse-contracts.md). The naive implementation —
`asyncio.wait_for(it.__anext__(), timeout=...)` per loop iteration — cancels
the in-flight anext() call on every timeout, which permanently exhausts the
underlying async generator (StopAsyncIteration on the next call, even though
more items remain). This pins the fix: a single anext() task must survive
across multiple timeout cycles.
"""

import asyncio

import pytest

from src.api.main import _SSE_HEARTBEAT_SENTINEL, _with_heartbeat


async def _slow_source():
    yield "first"
    await asyncio.sleep(0.3)
    yield "second"
    await asyncio.sleep(0.3)
    yield "third"


@pytest.mark.asyncio
async def test_heartbeat_survives_multiple_timeout_cycles():
    out = []
    async for item in _with_heartbeat(_slow_source(), interval_s=0.05):
        out.append(_SSE_HEARTBEAT_SENTINEL if item is _SSE_HEARTBEAT_SENTINEL else item)

    real_items = [i for i in out if i is not _SSE_HEARTBEAT_SENTINEL]
    heartbeats = [i for i in out if i is _SSE_HEARTBEAT_SENTINEL]

    assert real_items == ["first", "second", "third"]
    assert len(heartbeats) >= 2, "expected heartbeats during the two idle gaps"


@pytest.mark.asyncio
async def test_heartbeat_passthrough_when_no_idle_gap():
    async def fast_source():
        yield "a"
        yield "b"

    out = [item async for item in _with_heartbeat(fast_source(), interval_s=5.0)]
    assert out == ["a", "b"]


@pytest.mark.asyncio
async def test_heartbeat_propagates_source_exception():
    async def failing_source():
        yield "a"
        raise RuntimeError("boom")

    out = []
    with pytest.raises(RuntimeError, match="boom"):
        async for item in _with_heartbeat(failing_source(), interval_s=5.0):
            out.append(item)
    assert out == ["a"]
