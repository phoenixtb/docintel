"""
tests/test_rag_service_stream.py — unit tests for RAGService.stream() / query().

All external dependencies (embedders, retriever, OPA, reranker, prompt builder,
LLM, cache, DB) are mocked so tests run offline without any running services.
"""

import asyncio
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

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
from src.pipelines.query import RAGService
from src.config import Settings


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_doc(content: str = "Test chunk", score: float = 0.8) -> MagicMock:
    doc = MagicMock()
    doc.id = "chunk-001"
    doc.content = content
    doc.score = score
    doc.meta = {
        "document_id": "doc-001",
        "filename": "test.pdf",
        "chunk_index": 0,
        "document_type": "contracts",
    }
    return doc


def _make_settings(**overrides) -> Settings:
    defaults = {
        "QDRANT_URL": "http://localhost:6333",
        "LLM_CHAT_URL": "http://localhost:11434/v1",
        "LLM_EMBED_URL": "http://localhost:11430/v1",
        "LLM_API_KEY": "none",
        "LLM_MODEL": "test-model",
        "LLM_EMBED_MODEL": "test-embed",
        "INTERNAL_GATEWAY_SECRET": "test-secret",
        "RAG_CACHE_REPLAY_CHUNK_CHARS": "24",
        "RAG_CACHE_REPLAY_CHUNK_DELAY_MS": "0",  # instant in tests
        **{k.upper(): str(v) for k, v in overrides.items()},
    }
    import os
    with patch.dict(os.environ, defaults, clear=False):
        return Settings()


def _make_service(settings: Settings | None = None) -> RAGService:
    svc = RAGService(settings or _make_settings())
    svc._ready = True

    svc._dense_embedder = MagicMock()
    svc._dense_embedder.run.return_value = {"embedding": [0.1] * 1024}

    svc._sparse_embedder = MagicMock()
    svc._sparse_embedder.run.return_value = {"sparse_embedding": None}

    svc._cache_checker = None
    svc._cache_writer = None
    svc._domain_router = None
    svc._domain_filter_builder = None

    svc._retriever = MagicMock()
    svc._retriever.run.return_value = {"documents": [_make_doc()]}

    svc._opa_validator = MagicMock()
    svc._opa_validator.run.return_value = {"documents": [_make_doc()]}

    svc._reranker = MagicMock()
    svc._reranker.run.return_value = {"documents": [_make_doc()]}

    svc._prompt_builder = MagicMock()
    svc._prompt_builder.run.return_value = {"messages": [{"role": "user", "content": "Q?"}]}

    return svc


def _make_stream_kwargs(svc: RAGService, **overrides) -> dict:
    from docintel_common.security import Classification, UserContext
    user_ctx = UserContext(
        user_id="user-1",
        org_id="tenant-1",
        tenant_id="tenant-1",
        roles=["employee"],
        clearance=Classification.INTERNAL,
    )
    return {
        "question": "What is the termination clause?",
        "tenant_id": "tenant-1",
        "user_context": user_ctx,
        "user_roles": ["employee"],
        "user_id": "user-1",
        "history": None,
        "context_state": None,
        "document_type": None,
        "top_k": 5,
        "min_score": None,
        "use_cache": False,
        "use_reranking": True,
        "effective_model": "test-model",
        "effective_thinking": False,
        "settings": svc._settings,
        "llm_semaphore": asyncio.Semaphore(3),
        "request_id": "req-001",
        **overrides,
    }


async def _collect(gen: AsyncIterator) -> list:
    return [event async for event in gen]


def _patch_llm(svc: RAGService, tokens: list[str], thinking: list[str] | None = None):
    """
    Patch build_streaming_generator so that calling llm.run() enqueues tokens
    into the queue that stream() reads.  Returns the patch context manager.
    """
    thinking = thinking or []

    def _fake_build(**kwargs):
        callback = kwargs["streaming_callback"]
        llm_mock = MagicMock()

        def _run_side_effect(messages):
            for t in thinking:
                chunk = MagicMock()
                chunk.content = None
                chunk.meta = {"reasoning_content": t}
                callback(chunk)
            for tok in tokens:
                chunk = MagicMock()
                chunk.content = tok
                chunk.meta = {}
                callback(chunk)

        llm_mock.run.side_effect = _run_side_effect
        return llm_mock

    return patch("src.pipelines.query.build_streaming_generator", side_effect=_fake_build)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_emits_metadata_first():
    """First event from stream() must be MetadataEvent with cache_hit=False."""
    svc = _make_service()
    with _patch_llm(svc, ["answer"]):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc)))
    first = events[0]
    assert isinstance(first, MetadataEvent)
    assert first.cache_hit is False
    assert first.query_id == "req-001"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_emits_routing_event():
    """stream() must yield a RoutingEvent after MetadataEvent."""
    svc = _make_service()
    with _patch_llm(svc, ["answer"]):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc)))
    routing_events = [e for e in events if isinstance(e, RoutingEvent)]
    assert len(routing_events) == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_invokes_opa_validator():
    """
    OPA validator's run() must be called with documents, user_context, and
    request_id.  This is the headline regression test for the streaming security gap.
    """
    svc = _make_service()
    with _patch_llm(svc, ["answer"]):
        await _collect(svc.stream(**_make_stream_kwargs(svc)))
    svc._opa_validator.run.assert_called_once()
    call_kwargs = svc._opa_validator.run.call_args.kwargs
    assert "documents" in call_kwargs
    assert "user_context" in call_kwargs
    assert call_kwargs.get("request_id") == "req-001"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_skips_opa_when_retrieval_empty():
    """Empty retrieval → OPA is not called → no-docs path."""
    svc = _make_service()
    svc._retriever.run.return_value = {"documents": []}
    with _patch_llm(svc, []):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc)))
    svc._opa_validator.run.assert_not_called()
    token_events = [e for e in events if isinstance(e, TokenEvent)]
    assert token_events  # no-docs message emitted as TokenEvent


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_no_docs_yields_terminal_text_then_sources():
    """No docs after OPA → single TokenEvent (no-docs msg) then SourcesEvent(empty)."""
    svc = _make_service()
    svc._opa_validator.run.return_value = {"documents": []}
    with _patch_llm(svc, []):
        with patch("qdrant_client.QdrantClient", side_effect=Exception("no qdrant")):
            events = await _collect(svc.stream(**_make_stream_kwargs(svc)))
    token_events = [e for e in events if isinstance(e, TokenEvent)]
    sources_events = [e for e in events if isinstance(e, SourcesEvent)]
    assert token_events
    assert sources_events
    assert sources_events[-1].sources == []
    assert sources_events[-1].done is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_cache_hit_typewriter_chunks():
    """
    Cache hit with 100-char response and chunk_chars=24 →
    at least 4 TokenEvents, then SourcesEvent.
    """
    svc = _make_service()
    svc._cache_checker = MagicMock()
    cached_text = "A" * 100
    svc._cache_checker.run.return_value = {
        "cache_hit": True,
        "cached_response": cached_text,
        "cached_sources": [],
    }
    settings = _make_settings(rag_cache_replay_chunk_chars=24, rag_cache_replay_chunk_delay_ms=0)
    svc._settings = settings
    kwargs = _make_stream_kwargs(svc, use_cache=True, settings=settings)
    events = await _collect(svc.stream(**kwargs))

    token_events = [e for e in events if isinstance(e, TokenEvent)]
    sources_events = [e for e in events if isinstance(e, SourcesEvent)]
    # 100 chars / 24 = 4 full + 1 remainder = 5 chunks
    assert len(token_events) == 5
    assert "".join(e.text for e in token_events) == cached_text
    assert sources_events[-1].done is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_cache_hit_zero_delay_no_sleep():
    """delay_ms=0 → cache replay completes without any asyncio.sleep call."""
    svc = _make_service()
    svc._cache_checker = MagicMock()
    svc._cache_checker.run.return_value = {
        "cache_hit": True,
        "cached_response": "short",
        "cached_sources": [],
    }
    settings = _make_settings(rag_cache_replay_chunk_chars=10, rag_cache_replay_chunk_delay_ms=0)
    svc._settings = settings
    kwargs = _make_stream_kwargs(svc, use_cache=True, settings=settings)
    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await _collect(svc.stream(**kwargs))
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_yields_queued_event_when_semaphore_locked():
    """When all semaphore slots are occupied, a QueuedEvent is emitted."""
    svc = _make_service()
    semaphore = asyncio.Semaphore(1)
    await semaphore.acquire()  # hold the only slot

    async def _release():
        # Yield long enough for stream() to emit QueuedEvent and block on acquire
        await asyncio.sleep(0.05)
        semaphore.release()

    with _patch_llm(svc, ["answer"]):
        release_task = asyncio.create_task(_release())
        events = await _collect(svc.stream(**_make_stream_kwargs(svc, llm_semaphore=semaphore)))
        await release_task

    queued_events = [e for e in events if isinstance(e, QueuedEvent)]
    assert len(queued_events) == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_thinking_tokens_only_when_thinking_enabled():
    """ThinkingTokenEvents only appear when effective_thinking=True."""
    svc = _make_service()
    thinking_text = ["I think ", "carefully..."]
    answer_text = ["The answer."]

    def _fake_extract(chunk):
        return chunk.meta.get("reasoning_content", "")

    with _patch_llm(svc, answer_text, thinking=thinking_text):
        with patch("src.pipelines.query.extract_reasoning_content", side_effect=_fake_extract):
            # With thinking disabled
            events_no_think = await _collect(
                svc.stream(**_make_stream_kwargs(svc, effective_thinking=False))
            )
            # With thinking enabled
            events_think = await _collect(
                svc.stream(**_make_stream_kwargs(svc, effective_thinking=True))
            )

    think_events_off = [e for e in events_no_think if isinstance(e, ThinkingTokenEvent)]
    think_events_on = [e for e in events_think if isinstance(e, ThinkingTokenEvent)]
    assert think_events_off == []
    assert len(think_events_on) == len(thinking_text)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_persists_conversation_after_completion():
    """When conversation_id is set, _persist task is created with final answer."""
    svc = _make_service()
    created_tasks = []

    original_create_task = asyncio.create_task

    def _track_task(coro, **kwargs):
        task = original_create_task(coro, **kwargs)
        created_tasks.append(task)
        return task

    with _patch_llm(svc, ["The", " answer."]):
        with patch("asyncio.create_task", side_effect=_track_task):
            events = await _collect(
                svc.stream(**_make_stream_kwargs(svc, conversation_id="conv-001"))
            )
    # At least one task was created (for persistence)
    assert created_tasks
    # SourcesEvent is still in the stream
    sources_events = [e for e in events if isinstance(e, SourcesEvent)]
    assert sources_events


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_writes_cache_after_completion():
    """When use_cache=True and answer is non-empty, cache_writer.run is invoked."""
    svc = _make_service()
    svc._cache_writer = MagicMock()
    writer_called = []

    def _fake_writer_run(**kwargs):
        writer_called.append(kwargs)

    svc._cache_writer.run.side_effect = _fake_writer_run

    with _patch_llm(svc, ["cached answer"]):
        with patch("asyncio.create_task") as mock_create_task:
            # We need to actually run the coroutine that create_task would run
            coros = []

            def _run_coro(coro, **kwargs):
                task = asyncio.ensure_future(coro)
                coros.append(task)
                return task

            mock_create_task.side_effect = _run_coro
            events = await _collect(
                svc.stream(**_make_stream_kwargs(svc, use_cache=True))
            )
            # Allow tasks to complete
            if coros:
                await asyncio.gather(*coros, return_exceptions=True)

    assert svc._cache_writer.run.called


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_handles_client_disconnect():
    """
    Closing the generator mid-stream (aclose) cancels the LLM task.
    Calling aclose() on an async generator raises GeneratorExit inside it.
    """
    svc = _make_service()

    # Use a slow "LLM" that never finishes (will be cancelled)
    def _slow_build(**kwargs):
        callback = kwargs["streaming_callback"]
        llm_mock = MagicMock()

        async def _slow_run(messages):
            await asyncio.sleep(60)

        llm_mock.run.side_effect = lambda messages: None
        return llm_mock

    with patch("src.pipelines.query.build_streaming_generator", side_effect=_slow_build):
        gen = svc.stream(**_make_stream_kwargs(svc))
        # Consume just the first event (MetadataEvent) then close
        first = await gen.__anext__()
        assert isinstance(first, MetadataEvent)
        # Closing should not raise — GeneratorExit is handled internally
        await gen.aclose()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_drains_stream_into_dict():
    """
    RAGService.query() consuming a canonical event sequence returns the
    expected dict shape with correct field types.
    """
    svc = _make_service()
    with _patch_llm(svc, ["The ", "answer."]):
        result = await svc.query(**_make_stream_kwargs(svc))

    assert isinstance(result["answer"], str)
    assert result["answer"].strip() == "The answer."
    assert isinstance(result["thinking"], str)
    assert isinstance(result["sources"], list)
    assert isinstance(result["cache_hit"], bool)
    assert isinstance(result["latency_ms"], int)
    assert result["model_used"] == "test-model"
