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


def _patch_llm(
    svc: RAGService,
    tokens: list[str],
    thinking: list[str] | None = None,
    usage: dict | None = None,
):
    """
    Patch build_streaming_generator so that calling llm.run() enqueues tokens
    into the queue that stream() reads.  Returns the patch context manager.

    usage: if given, emits a trailing no-content chunk carrying
    meta["usage"] — mirrors the real trailing chunk an engine sends when it
    honours stream_options.include_usage (see llm_adapter.extract_usage).
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
            if usage is not None:
                chunk = MagicMock()
                chunk.content = ""
                chunk.meta = {"usage": usage}
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


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_captures_token_usage_when_engine_reports_it():
    """
    A5: when the engine sends a trailing usage chunk, RAGService records
    prompt/completion tokens via CostTracker on _last_tokens_used/_last_cost_usd
    (read by api/main.py after the stream completes).
    """
    svc = _make_service()
    usage = {"prompt_tokens": 120, "completion_tokens": 45, "total_tokens": 165}
    with _patch_llm(svc, ["The ", "answer."], usage=usage):
        await _collect(svc.stream(**_make_stream_kwargs(svc)))

    assert svc._last_tokens_used == {"prompt": 120, "completion": 45}
    assert isinstance(svc._last_cost_usd, float)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_zero_tokens_when_engine_omits_usage():
    """Engines that don't honour stream_options.include_usage leave usage absent
    — CostTracker must degrade to zero tokens/cost, not raise."""
    svc = _make_service()
    with _patch_llm(svc, ["The ", "answer."]):
        await _collect(svc.stream(**_make_stream_kwargs(svc)))

    assert svc._last_tokens_used == {"prompt": 0, "completion": 0}
    assert svc._last_cost_usd == 0.0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_result_includes_tokens_used_and_cost():
    """query() dict result surfaces tokens_used/cost_usd for the /query handler."""
    svc = _make_service()
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    with _patch_llm(svc, ["answer"], usage=usage):
        result = await svc.query(**_make_stream_kwargs(svc))

    assert result["tokens_used"] == {"prompt": 10, "completion": 5}
    assert isinstance(result["cost_usd"], float)


# ---------------------------------------------------------------------------
# B3/B4 — retrieval mode, rerank candidate counts, reranker_degraded, trace_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_emits_retrieval_mode_metadata():
    """Retriever reports retrieval_mode; stream() must surface it in a MetadataEvent."""
    svc = _make_service()
    svc._retriever.run.return_value = {"documents": [_make_doc()], "retrieval_mode": "hybrid"}
    with _patch_llm(svc, ["answer"]):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc)))

    modes = [e.retrieval_mode for e in events if isinstance(e, MetadataEvent) and e.retrieval_mode is not None]
    assert modes == ["hybrid"]
    assert svc._last_retrieval_mode == "hybrid"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_emits_rerank_candidate_counts():
    """Reranker receives N docs and returns M — stream() must report both counts."""
    svc = _make_service()
    svc._retriever.run.return_value = {"documents": [_make_doc(), _make_doc(), _make_doc()]}
    svc._opa_validator.run.return_value = {"documents": [_make_doc(), _make_doc(), _make_doc()]}
    svc._reranker.run.return_value = {"documents": [_make_doc()], "reranker_degraded": False}
    with _patch_llm(svc, ["answer"]):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc)))

    meta_with_counts = [
        e for e in events
        if isinstance(e, MetadataEvent) and e.rerank_candidates_in is not None
    ]
    assert len(meta_with_counts) == 1
    assert meta_with_counts[0].rerank_candidates_in == 3
    assert meta_with_counts[0].rerank_candidates_out == 1
    assert meta_with_counts[0].reranker_degraded is False
    assert svc._last_rerank_candidates_in == 3
    assert svc._last_rerank_candidates_out == 1
    assert svc._last_reranker_degraded is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_reranker_failure_marks_degraded_with_counts():
    """Reranker exception → degraded=True, candidates_out falls back to candidates_in."""
    svc = _make_service()
    svc._retriever.run.return_value = {"documents": [_make_doc(), _make_doc()]}
    svc._opa_validator.run.return_value = {"documents": [_make_doc(), _make_doc()]}
    svc._reranker.run.side_effect = RuntimeError("reranker unreachable")
    with _patch_llm(svc, ["answer"]):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc)))

    degraded_events = [
        e for e in events
        if isinstance(e, MetadataEvent) and e.reranker_degraded is True
    ]
    assert degraded_events
    assert svc._last_reranker_degraded is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_no_trace_id_when_tracer_absent():
    """No tracer passed in → _last_trace_id is empty (no Langfuse dependency)."""
    svc = _make_service()
    with _patch_llm(svc, ["answer"]):
        await _collect(svc.stream(**_make_stream_kwargs(svc)))
    assert svc._last_trace_id == ""


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_creates_trace_with_request_id_when_tracer_enabled():
    """B4 — when the tracer is enabled, the Langfuse trace id equals request_id
    so ClickHouse's trace_id column can deep-link straight to it."""
    svc = _make_service()
    mock_tracer = MagicMock()
    mock_tracer.enabled = True
    mock_trace = MagicMock()
    mock_tracer.start_trace.return_value = mock_trace

    with _patch_llm(svc, ["answer"]):
        await _collect(svc.stream(**_make_stream_kwargs(svc, tracer=mock_tracer)))

    mock_tracer.start_trace.assert_called_once()
    assert mock_tracer.start_trace.call_args.kwargs["trace_id"] == "req-001"
    assert svc._last_trace_id == "req-001"
    mock_trace.update.assert_called_once()
    mock_trace.flush.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_result_includes_retrieval_and_rerank_fields():
    """query() dict aggregates the new B3/B4 fields for the non-streaming /query path."""
    svc = _make_service()
    svc._retriever.run.return_value = {"documents": [_make_doc(), _make_doc()], "retrieval_mode": "dense"}
    svc._opa_validator.run.return_value = {"documents": [_make_doc(), _make_doc()]}
    svc._reranker.run.return_value = {"documents": [_make_doc()], "reranker_degraded": False}
    with _patch_llm(svc, ["answer"]):
        result = await svc.query(**_make_stream_kwargs(svc))

    assert result["retrieval_mode"] == "dense"
    assert result["rerank_candidates_in"] == 2
    assert result["rerank_candidates_out"] == 1
    assert result["reranker_degraded"] is False
    assert result["trace_id"] == ""


# ---------------------------------------------------------------------------
# G2 — query expansion wiring
# ---------------------------------------------------------------------------

def _make_doc_id(doc_id: str, score: float = 0.8) -> MagicMock:
    doc = _make_doc(score=score)
    doc.id = doc_id
    return doc


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_expansion_disabled_by_default_skips_expander():
    """use_query_expansion=False (default) — expander never instantiated/called,
    retriever.run is called exactly once (no union retrieval)."""
    svc = _make_service()
    svc._query_expander = MagicMock()  # present but must not be consulted
    assert svc._settings.use_query_expansion is False
    with _patch_llm(svc, ["answer"]):
        await _collect(svc.stream(**_make_stream_kwargs(svc)))
    svc._query_expander.run.assert_not_called()
    svc._retriever.run.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_expansion_unions_candidates_before_rerank():
    """Enabled + distinct expanded query → expander called, dense embedder
    called twice (original + expanded), retriever called twice, and the
    union (deduped by id) is what reaches the reranker."""
    settings = _make_settings(use_query_expansion=True)
    svc = _make_service(settings)
    svc._query_expander = MagicMock()
    svc._query_expander.run.return_value = {
        "original_query": "remote work",
        "expanded_query": "remote work WFH telecommute",
        "search_terms": ["remote work", "WFH", "telecommute"],
    }
    original_doc = _make_doc_id("chunk-original")
    expanded_only_doc = _make_doc_id("chunk-expanded-only")
    svc._retriever.run.side_effect = [
        {"documents": [original_doc], "retrieval_mode": "hybrid"},
        {"documents": [original_doc, expanded_only_doc]},  # expanded-query retrieval
    ]
    svc._opa_validator.run.side_effect = lambda documents, **kw: {"documents": documents}
    svc._reranker.run.side_effect = lambda query, documents: {"documents": documents, "reranker_degraded": False}

    with _patch_llm(svc, ["answer"]):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc, settings=settings)))

    assert svc._dense_embedder.run.call_count == 2
    assert svc._retriever.run.call_count == 2
    rerank_call_docs = svc._reranker.run.call_args.kwargs["documents"]
    assert {d.id for d in rerank_call_docs} == {"chunk-original", "chunk-expanded-only"}

    meta_events = [e for e in events if isinstance(e, MetadataEvent) and e.query_expanded is not None]
    assert meta_events and meta_events[0].query_expanded is True
    assert svc._last_query_expanded is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_expansion_noop_when_rewrite_equals_original():
    """Expander returns the same text back (no useful rewrite) — no second
    embed/retrieve call, query_expanded stays False."""
    settings = _make_settings(use_query_expansion=True)
    svc = _make_service(settings)
    svc._query_expander = MagicMock()
    svc._query_expander.run.return_value = {
        "original_query": "What is the termination clause?",
        "expanded_query": "What is the termination clause?",
        "search_terms": ["What is the termination clause?"],
    }
    with _patch_llm(svc, ["answer"]):
        await _collect(svc.stream(**_make_stream_kwargs(svc, settings=settings)))

    assert svc._dense_embedder.run.call_count == 1
    assert svc._retriever.run.call_count == 1
    assert svc._last_query_expanded is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_expansion_fails_open_on_expander_exception():
    """Expander raises (e.g. LLM unreachable) — stream() falls back to the
    original query only; no crash, query_expanded=False."""
    settings = _make_settings(use_query_expansion=True)
    svc = _make_service(settings)
    svc._query_expander = MagicMock()
    svc._query_expander.run.side_effect = RuntimeError("LMForge unreachable")

    with _patch_llm(svc, ["answer"]):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc, settings=settings)))

    assert svc._dense_embedder.run.call_count == 1
    assert svc._retriever.run.call_count == 1
    assert svc._last_query_expanded is False
    sources_events = [e for e in events if isinstance(e, SourcesEvent)]
    assert sources_events  # stream completed normally despite expander failure


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_expansion_fails_open_on_timeout():
    """Expander call exceeds rag_query_expansion_timeout_s — asyncio.wait_for
    raises TimeoutError, caught and treated identically to any other failure."""
    settings = _make_settings(use_query_expansion=True, rag_query_expansion_timeout_s=0.01)
    svc = _make_service(settings)

    def _slow_run(query):
        import time
        time.sleep(0.2)
        return {"expanded_query": query + " slow"}

    svc._query_expander = MagicMock()
    svc._query_expander.run.side_effect = _slow_run

    with _patch_llm(svc, ["answer"]):
        events = await _collect(svc.stream(**_make_stream_kwargs(svc, settings=settings)))

    assert svc._last_query_expanded is False
    assert svc._retriever.run.call_count == 1
    assert any(isinstance(e, SourcesEvent) for e in events)
