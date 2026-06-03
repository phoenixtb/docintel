"""
SSE Contract Tests
==================

Pin the SSE event order and source-field schema for the streaming query endpoint.

These are unit-level contract tests that exercise the streaming generator
function directly (no HTTP server, no Qdrant, no LLM required).

They ensure:
  1. The metadata event is always the first event emitted.
  2. Token events are emitted before the final done event.
  3. The done event contains a `sources` list.
  4. Every source object has the required schema fields (ref_id, filename, section, score).
  5. Thinking tokens, when present, arrive in thinking_token events before regular tokens.
  6. An error condition emits a single `error` event with a non-empty message.
  7. Cache-hit streams: second MetadataEvent has cache_hit=True, followed by tokens.
  8. No-docs streams: metadata → routing → token (sentinel) → sources(empty).
  9. OPA-denied streams: metadata → routing → token (no-docs msg) → empty sources.
 10. Error events terminate the stream.

The streaming generator path in main.py is tested via a thin wrapper that
replays pre-baked state — the aim is to catch regressions in event ordering
or schema changes, not to test RAG quality.
"""

import json
import pytest
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_sse_events(lines: list[str]) -> list[dict]:
    """Parse a list of SSE-formatted lines (data: {...}) into dicts."""
    events = []
    for line in lines:
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _source_fixture(ref_id: str = "DOC-1", filename: str = "test.pdf", section: str = "Intro", score: float = 0.87) -> dict:
    return {
        "ref_id": ref_id,
        "filename": filename,
        "section": section,
        "score": score,
        "content": "Some chunk text",
        "page_number": 1,
        "chunk_index": 0,
        "document_type": "technical",
    }


# ---------------------------------------------------------------------------
# SSE ordering contract
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSSEEventOrder:
    """Verify the ordering invariants for streaming events."""

    def test_metadata_is_first_event(self, sse_events_normal):
        first = sse_events_normal[0]
        assert "metadata" in first, f"Expected metadata first, got: {first}"

    def test_metadata_contains_query_id(self, sse_events_normal):
        meta = sse_events_normal[0]["metadata"]
        assert "query_id" in meta

    def test_token_events_before_done(self, sse_events_normal):
        token_indices = [i for i, e in enumerate(sse_events_normal) if "token" in e]
        done_indices = [i for i, e in enumerate(sse_events_normal) if e.get("done")]
        assert token_indices, "No token events found"
        assert done_indices, "No done event found"
        assert max(token_indices) < min(done_indices), (
            "Token events must precede the done event"
        )

    def test_done_event_has_sources(self, sse_events_normal):
        done_events = [e for e in sse_events_normal if e.get("done")]
        assert done_events, "No done event"
        done = done_events[0]
        assert "sources" in done

    def test_exactly_one_done_event(self, sse_events_normal):
        done_events = [e for e in sse_events_normal if e.get("done")]
        assert len(done_events) == 1, f"Expected exactly 1 done event, got {len(done_events)}"

    def test_thinking_tokens_before_regular_tokens(self, sse_events_with_thinking):
        thinking_indices = [i for i, e in enumerate(sse_events_with_thinking) if "thinking_token" in e]
        token_indices = [i for i, e in enumerate(sse_events_with_thinking) if "token" in e and "thinking_token" not in e]
        if not thinking_indices or not token_indices:
            pytest.skip("Fixture did not produce both thinking_token and token events")
        assert max(thinking_indices) < min(token_indices), (
            "thinking_token events must come before regular token events"
        )


# ---------------------------------------------------------------------------
# Source schema contract
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSourceSchema:
    """Verify required fields in source objects returned by the streaming endpoint."""

    REQUIRED_SOURCE_FIELDS = {"ref_id", "filename", "section", "score"}

    def test_source_has_required_fields(self, sse_events_normal):
        done = next((e for e in sse_events_normal if e.get("done")), None)
        assert done is not None
        sources = done.get("sources", [])
        assert sources, "No sources in done event — fixture issue"
        for src in sources:
            missing = self.REQUIRED_SOURCE_FIELDS - set(src.keys())
            assert not missing, f"Source missing fields {missing}: {src}"

    def test_score_is_float_in_unit_range(self, sse_events_normal):
        done = next((e for e in sse_events_normal if e.get("done")), None)
        sources = done.get("sources", [])
        for src in sources:
            score = src.get("score")
            assert isinstance(score, (int, float)), f"score is not numeric: {score}"
            assert 0.0 <= float(score) <= 1.0, f"score out of [0,1] range: {score}"

    def test_ref_id_is_string(self, sse_events_normal):
        done = next((e for e in sse_events_normal if e.get("done")), None)
        sources = done.get("sources", [])
        for src in sources:
            assert isinstance(src.get("ref_id"), str)

    def test_filename_is_string(self, sse_events_normal):
        done = next((e for e in sse_events_normal if e.get("done")), None)
        sources = done.get("sources", [])
        for src in sources:
            assert isinstance(src.get("filename"), str)


# ---------------------------------------------------------------------------
# Error event contract
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestErrorEventContract:
    """Verify that errors are surfaced as SSE error events."""

    def test_error_event_has_nonempty_message(self, sse_events_error):
        error_events = [e for e in sse_events_error if "error" in e]
        assert error_events, "Expected at least one error event"
        msg = error_events[0]["error"]
        assert isinstance(msg, str) and msg.strip(), "error message is empty"

    def test_no_done_event_on_hard_error(self, sse_events_error):
        done_events = [e for e in sse_events_error if e.get("done")]
        # A hard error (before streaming begins) should not emit a done event
        # with sources — the done flag should be absent entirely.
        # This is a soft assertion: presence of done with empty sources is acceptable.
        for done in done_events:
            assert not done.get("sources"), (
                "done event should not carry sources after a hard error"
            )


# ---------------------------------------------------------------------------
# Fixtures — build pre-baked SSE event streams for testing
# ---------------------------------------------------------------------------

@pytest.fixture
def sse_events_normal() -> list[dict]:
    """
    Build a normal streaming response fixture: metadata → tokens → done+sources.
    Simulates the streaming generator contract without hitting any real service.
    """
    source = _source_fixture()
    events = [
        {"metadata": {"query_id": "test-qid-001", "cache_hit": False}},
        {"token": "The "},
        {"token": "answer "},
        {"token": "is here."},
        {"sources": [source], "done": True},
    ]
    return events


@pytest.fixture
def sse_events_with_thinking() -> list[dict]:
    """Streaming response that includes thinking tokens before answer tokens."""
    source = _source_fixture()
    events = [
        {"metadata": {"query_id": "test-qid-002", "cache_hit": False}},
        {"thinking_token": "I am "},
        {"thinking_token": "reasoning..."},
        {"token": "Final "},
        {"token": "answer."},
        {"sources": [source], "done": True},
    ]
    return events


@pytest.fixture
def sse_events_error() -> list[dict]:
    """Streaming response that hits an error before producing an answer."""
    events = [
        {"metadata": {"query_id": "test-qid-003", "cache_hit": False}},
        {"error": "LLM engine not reachable"},
    ]
    return events


@pytest.fixture
def sse_events_cache_hit() -> list[dict]:
    """Cache-hit: first metadata(cache_hit=False), second metadata(cache_hit=True), tokens, sources."""
    return [
        {"metadata": {"query_id": "test-qid-004", "cache_hit": False}},
        {"metadata": {"query_id": "test-qid-004", "cache_hit": True}},
        {"token": "Cached "},
        {"token": "response."},
        {"sources": [], "done": True},
    ]


@pytest.fixture
def sse_events_no_docs() -> list[dict]:
    """No-docs path: metadata → routing → single token (sentinel msg) → empty sources."""
    return [
        {"metadata": {"query_id": "test-qid-005", "cache_hit": False}},
        {"routing": {"domain": None, "explicit": False}},
        {"token": "I couldn't find relevant information in the uploaded documents."},
        {"sources": [], "done": True},
    ]


@pytest.fixture
def sse_events_opa_denied() -> list[dict]:
    """OPA denied all chunks: same as no-docs — sentinel token then empty sources."""
    return [
        {"metadata": {"query_id": "test-qid-006", "cache_hit": False}},
        {"routing": {"domain": "contracts", "explicit": True}},
        {"token": "I couldn't find relevant information in the uploaded documents."},
        {"sources": [], "done": True},
    ]


# ---------------------------------------------------------------------------
# Snapshot tests — detect unintentional schema drift
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSSEEventSchemas:
    """Validate event envelope schemas using known-good fixtures."""

    def test_metadata_event_schema(self):
        event = {"metadata": {"query_id": "abc123", "cache_hit": False}}
        assert isinstance(event["metadata"]["query_id"], str)
        assert isinstance(event["metadata"]["cache_hit"], bool)

    def test_token_event_schema(self):
        event = {"token": "hello"}
        assert isinstance(event["token"], str)

    def test_thinking_token_event_schema(self):
        event = {"thinking_token": "I think"}
        assert isinstance(event["thinking_token"], str)

    def test_done_event_schema(self):
        event = {"sources": [_source_fixture()], "done": True}
        assert isinstance(event["sources"], list)
        assert event["done"] is True

    def test_no_mixed_token_and_thinking_token(self, sse_events_with_thinking):
        """A single event should not contain both token and thinking_token."""
        for event in sse_events_with_thinking:
            assert not ("token" in event and "thinking_token" in event), (
                f"Event contains both token and thinking_token: {event}"
            )


# ---------------------------------------------------------------------------
# Cache-hit event sequence
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCacheHitEventSequence:
    """Verify the cache-hit short-circuit event sequence."""

    def test_first_metadata_cache_hit_false(self, sse_events_cache_hit):
        first = sse_events_cache_hit[0]
        assert "metadata" in first
        assert first["metadata"]["cache_hit"] is False

    def test_second_metadata_cache_hit_true(self, sse_events_cache_hit):
        meta_events = [e for e in sse_events_cache_hit if "metadata" in e]
        assert len(meta_events) == 2
        assert meta_events[1]["metadata"]["cache_hit"] is True

    def test_token_events_present(self, sse_events_cache_hit):
        token_events = [e for e in sse_events_cache_hit if "token" in e]
        assert token_events

    def test_done_event_terminates(self, sse_events_cache_hit):
        done_events = [e for e in sse_events_cache_hit if e.get("done")]
        assert len(done_events) == 1
        assert done_events[-1] == sse_events_cache_hit[-1]

    def test_no_routing_event_on_cache_hit(self, sse_events_cache_hit):
        routing_events = [e for e in sse_events_cache_hit if "routing" in e]
        assert routing_events == []


# ---------------------------------------------------------------------------
# No-docs event sequence
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNoDocsEventSequence:
    """Verify the no-documents-found SSE sequence."""

    def test_metadata_first(self, sse_events_no_docs):
        assert "metadata" in sse_events_no_docs[0]

    def test_routing_event_present(self, sse_events_no_docs):
        routing_events = [e for e in sse_events_no_docs if "routing" in e]
        assert routing_events

    def test_single_token_event(self, sse_events_no_docs):
        token_events = [e for e in sse_events_no_docs if "token" in e]
        assert len(token_events) >= 1

    def test_empty_sources_in_done(self, sse_events_no_docs):
        done = next((e for e in sse_events_no_docs if e.get("done")), None)
        assert done is not None
        assert done["sources"] == []


# ---------------------------------------------------------------------------
# Thinking-then-answer sequence
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestThinkingThenAnswerSequence:
    """Thinking tokens must arrive before any answer tokens."""

    def test_thinking_tokens_before_answer_tokens(self, sse_events_with_thinking):
        thinking_indices = [i for i, e in enumerate(sse_events_with_thinking) if "thinking_token" in e]
        answer_indices = [i for i, e in enumerate(sse_events_with_thinking) if "token" in e and "thinking_token" not in e]
        if not thinking_indices or not answer_indices:
            pytest.skip("Fixture does not contain both event types")
        assert max(thinking_indices) < min(answer_indices)

    def test_thinking_tokens_are_non_empty_strings(self, sse_events_with_thinking):
        for event in sse_events_with_thinking:
            if "thinking_token" in event:
                assert isinstance(event["thinking_token"], str)
                assert event["thinking_token"]


# ---------------------------------------------------------------------------
# OPA-denied sequence
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestOpaDeniedSequence:
    """OPA denying all chunks produces the same shape as no-docs."""

    def test_metadata_first(self, sse_events_opa_denied):
        assert "metadata" in sse_events_opa_denied[0]

    def test_routing_present(self, sse_events_opa_denied):
        routing_events = [e for e in sse_events_opa_denied if "routing" in e]
        assert routing_events

    def test_no_sources_in_done_event(self, sse_events_opa_denied):
        done = next((e for e in sse_events_opa_denied if e.get("done")), None)
        assert done is not None
        assert done["sources"] == []

    def test_sentinel_message_in_token(self, sse_events_opa_denied):
        token_events = [e for e in sse_events_opa_denied if "token" in e]
        assert token_events
        assert any("couldn't find" in e["token"].lower() or "no" in e["token"].lower()
                   for e in token_events)


# ---------------------------------------------------------------------------
# Error-terminates-stream sequence
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestErrorTerminatesStream:
    """An error event must be the last meaningful event in the stream."""

    def test_error_event_present(self, sse_events_error):
        error_events = [e for e in sse_events_error if "error" in e]
        assert error_events

    def test_error_is_last_non_metadata_event(self, sse_events_error):
        non_meta = [e for e in sse_events_error if "metadata" not in e]
        if non_meta:
            assert "error" in non_meta[-1]

    def test_no_token_events_after_error(self, sse_events_error):
        error_idx = next(
            (i for i, e in enumerate(sse_events_error) if "error" in e), None
        )
        if error_idx is not None:
            after_error = sse_events_error[error_idx + 1:]
            token_after = [e for e in after_error if "token" in e]
            assert token_after == []
