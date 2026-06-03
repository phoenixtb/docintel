"""
tests/test_dependencies.py — unit tests for ConversationHistoryDep.

These tests exercise the get_conversation_history() dependency function
in isolation (no HTTP server, no DB, no Qdrant required).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.api.dependencies import LoadedHistory, get_conversation_history
from src.api.schemas import QueryRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(conversation_id: str | None = None) -> QueryRequest:
    return QueryRequest(
        question="Test question?",
        conversation_id=conversation_id,
    )


def _make_rag_service(history_result=None, raises=None):
    svc = MagicMock()
    if raises:
        svc._load_conversation_history.side_effect = raises
    else:
        svc._load_conversation_history.return_value = history_result or ([], {})
    return svc


def _make_user_ctx(tenant_id: str = "tenant-1"):
    ctx = MagicMock()
    ctx.tenant_id = tenant_id
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
async def test_history_dep_returns_empty_when_no_conversation_id():
    """No conversation_id → LoadedHistory with empty messages and context_state."""
    request = _make_request(conversation_id=None)
    svc = _make_rag_service()
    ctx = _make_user_ctx()

    with patch("src.api.dependencies._run_db") as mock_run_db:
        result = await get_conversation_history(request, svc, ctx)

    assert isinstance(result, LoadedHistory)
    assert result.messages == []
    assert result.context_state == {}
    mock_run_db.assert_not_called()
    svc._load_conversation_history.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_history_dep_loads_history_when_conversation_id_present():
    """With conversation_id, DB is queried and messages/context_state are returned."""
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
    ]
    context_state = {"has_summary": False, "summarized_turns": 0, "verbatim_turns": 1}

    request = _make_request(conversation_id="conv-abc123")
    svc = _make_rag_service(history_result=(messages, context_state))
    ctx = _make_user_ctx(tenant_id="tenant-2")

    async def _fake_run_db(fn):
        return fn()

    with patch("src.api.dependencies._run_db", side_effect=_fake_run_db):
        result = await get_conversation_history(request, svc, ctx)

    assert isinstance(result, LoadedHistory)
    assert result.messages == messages
    assert result.context_state == context_state
    svc._load_conversation_history.assert_called_once_with("conv-abc123", "tenant-2")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_history_dep_propagates_db_error():
    """DB errors from _run_db propagate as HTTPException 500."""
    from fastapi import HTTPException

    request = _make_request(conversation_id="conv-error")
    svc = _make_rag_service()
    ctx = _make_user_ctx()

    async def _failing_run_db(fn):
        raise RuntimeError("DB connection failed")

    with patch("src.api.dependencies._run_db", side_effect=_failing_run_db):
        with pytest.raises(HTTPException) as exc_info:
            await get_conversation_history(request, svc, ctx)

    assert exc_info.value.status_code == 500
    assert "conversation history" in exc_info.value.detail.lower()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_history_dep_with_summary_in_context_state():
    """Context state with has_summary=True is preserved in LoadedHistory."""
    messages = [{"role": "system", "content": "Earlier: user asked about policy"}]
    context_state = {"has_summary": True, "summarized_turns": 3, "verbatim_turns": 2}

    request = _make_request(conversation_id="conv-summarized")
    svc = _make_rag_service(history_result=(messages, context_state))
    ctx = _make_user_ctx()

    async def _fake_run_db(fn):
        return fn()

    with patch("src.api.dependencies._run_db", side_effect=_fake_run_db):
        result = await get_conversation_history(request, svc, ctx)

    assert result.context_state["has_summary"] is True
    assert result.context_state["summarized_turns"] == 3
    assert len(result.messages) == 1


@pytest.mark.unit
def test_loaded_history_defaults():
    """LoadedHistory defaults produce empty messages and context_state."""
    h = LoadedHistory()
    assert h.messages == []
    assert h.context_state == {}


@pytest.mark.unit
def test_loaded_history_construction():
    """LoadedHistory stores provided values."""
    msgs = [{"role": "user", "content": "Q"}]
    cs = {"has_summary": True}
    h = LoadedHistory(messages=msgs, context_state=cs)
    assert h.messages is msgs
    assert h.context_state is cs
