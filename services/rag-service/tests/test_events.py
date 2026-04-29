"""
tests/test_events.py — wire-format pin tests for PipelineEvent serialisation.

These tests assert the exact JSON shape that _serialize_sse() produces for
each event type.  They are the safety net for any accidental schema drift.
"""

import json
import pytest

from src.events import (
    ErrorEvent,
    MetadataEvent,
    QueuedEvent,
    RoutingEvent,
    SourcesEvent,
    ThinkingTokenEvent,
    TokenEvent,
)
from src.api.main import _serialize_sse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse(sse_line: str) -> dict:
    """Parse a single SSE data line into a dict."""
    assert sse_line.startswith("data: "), f"Not a data line: {sse_line!r}"
    assert sse_line.endswith("\n\n"), f"Missing trailing newlines: {sse_line!r}"
    return json.loads(sse_line[6:])


# ---------------------------------------------------------------------------
# MetadataEvent
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMetadataEventSerialisation:
    def test_cache_miss_shape(self):
        event = MetadataEvent(query_id="qid-001", cache_hit=False)
        payload = _parse(_serialize_sse(event))
        assert payload == {"metadata": {"query_id": "qid-001", "cache_hit": False}}

    def test_cache_hit_shape(self):
        event = MetadataEvent(query_id="qid-002", cache_hit=True)
        payload = _parse(_serialize_sse(event))
        assert payload["metadata"]["cache_hit"] is True

    def test_context_state_included_when_provided(self):
        cs = {"has_summary": True, "summarized_turns": 3, "verbatim_turns": 2}
        event = MetadataEvent(query_id="qid-003", cache_hit=False, context_state=cs)
        payload = _parse(_serialize_sse(event))
        assert payload["metadata"]["context_state"] == cs

    def test_context_state_absent_when_none(self):
        event = MetadataEvent(query_id="qid-004", cache_hit=False, context_state=None)
        payload = _parse(_serialize_sse(event))
        assert "context_state" not in payload["metadata"]

    def test_query_id_is_string(self):
        event = MetadataEvent(query_id="abc-123", cache_hit=False)
        payload = _parse(_serialize_sse(event))
        assert isinstance(payload["metadata"]["query_id"], str)

    def test_cache_hit_is_bool(self):
        event = MetadataEvent(query_id="abc-123", cache_hit=False)
        payload = _parse(_serialize_sse(event))
        assert isinstance(payload["metadata"]["cache_hit"], bool)


# ---------------------------------------------------------------------------
# RoutingEvent
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRoutingEventSerialisation:
    def test_domain_and_explicit_shape(self):
        event = RoutingEvent(domain="contracts", explicit=True)
        payload = _parse(_serialize_sse(event))
        assert payload == {"routing": {"domain": "contracts", "explicit": True}}

    def test_no_domain(self):
        event = RoutingEvent(domain=None, explicit=False)
        payload = _parse(_serialize_sse(event))
        assert payload["routing"]["domain"] is None
        assert payload["routing"]["explicit"] is False


# ---------------------------------------------------------------------------
# QueuedEvent
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestQueuedEventSerialisation:
    def test_default_message_shape(self):
        event = QueuedEvent()
        payload = _parse(_serialize_sse(event))
        assert payload["queued"] is True
        assert isinstance(payload["message"], str)
        assert payload["message"]

    def test_custom_message(self):
        event = QueuedEvent(message="Custom wait message")
        payload = _parse(_serialize_sse(event))
        assert payload["message"] == "Custom wait message"


# ---------------------------------------------------------------------------
# ThinkingTokenEvent
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestThinkingTokenEventSerialisation:
    def test_shape(self):
        event = ThinkingTokenEvent(text="I am reasoning...")
        payload = _parse(_serialize_sse(event))
        assert payload == {"thinking_token": "I am reasoning..."}

    def test_text_is_string(self):
        event = ThinkingTokenEvent(text="token")
        payload = _parse(_serialize_sse(event))
        assert isinstance(payload["thinking_token"], str)

    def test_no_token_key(self):
        event = ThinkingTokenEvent(text="t")
        payload = _parse(_serialize_sse(event))
        assert "token" not in payload


# ---------------------------------------------------------------------------
# TokenEvent
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTokenEventSerialisation:
    def test_shape(self):
        event = TokenEvent(text="Hello")
        payload = _parse(_serialize_sse(event))
        assert payload == {"token": "Hello"}

    def test_no_thinking_token_key(self):
        event = TokenEvent(text="t")
        payload = _parse(_serialize_sse(event))
        assert "thinking_token" not in payload


# ---------------------------------------------------------------------------
# SourcesEvent
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSourcesEventSerialisation:
    def test_done_true_shape(self):
        sources = [{"ref_id": 1, "filename": "doc.pdf", "score": 0.9, "section": "p. 1"}]
        event = SourcesEvent(sources=sources, done=True)
        payload = _parse(_serialize_sse(event))
        assert payload["done"] is True
        assert isinstance(payload["sources"], list)
        assert payload["sources"][0]["ref_id"] == 1

    def test_empty_sources(self):
        event = SourcesEvent(sources=[], done=True)
        payload = _parse(_serialize_sse(event))
        assert payload["sources"] == []
        assert payload["done"] is True


# ---------------------------------------------------------------------------
# ErrorEvent
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestErrorEventSerialisation:
    def test_shape(self):
        event = ErrorEvent(message="Something went wrong")
        payload = _parse(_serialize_sse(event))
        assert payload == {"error": "Something went wrong"}

    def test_message_is_string(self):
        event = ErrorEvent(message="oops")
        payload = _parse(_serialize_sse(event))
        assert isinstance(payload["error"], str)
