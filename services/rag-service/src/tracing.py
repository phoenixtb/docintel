"""
Langfuse Tracing Integration
============================

Provides observability for the RAG service using the Langfuse Python SDK.

Haystack 2.x native tracing (OpenTelemetry / LangfuseConnector) requires
haystack-experimental and Langfuse SDK v3+. Until we upgrade, this module
provides a lightweight wrapper that:

  - Creates a top-level Langfuse trace per query
  - Attaches spans for service-layer steps (embedding, cache, domain routing)
  - The Haystack Pipeline's internal component runs are instrumented
    automatically when HAYSTACK_CONTENT_TRACING_ENABLED=true is set and
    a compatible tracer is registered

Usage:
    tracer = LangfuseTracer(settings)
    with tracer.trace("rag_query", inputs={...}, user_id=...) as t:
        ...
        with t.span("cache_check"):
            ...
"""

import logging
from contextlib import contextmanager
from typing import Any, Generator, Optional

from .config import Settings, get_settings

logger = logging.getLogger(__name__)


class _Span:
    """Thin wrapper around a Langfuse span. No-ops when Langfuse is off."""

    def __init__(self, span: Any | None) -> None:
        self._span = span

    def end(self, output: dict | None = None) -> None:
        if self._span is None:
            return
        if output:
            self._span.update(output=output)
        self._span.end()

    def error(self, message: str) -> None:
        if self._span:
            self._span.update(level="ERROR", status_message=message)


class _Trace:
    """Thin wrapper around a Langfuse trace context."""

    def __init__(self, trace: Any | None, client: Any | None) -> None:
        self._trace = trace
        self._client = client

    @contextmanager
    def span(self, name: str, input: dict | None = None) -> Generator[_Span, None, None]:
        if self._trace is None:
            yield _Span(None)
            return

        lf_span = self._trace.span(name=name, input=input or {})
        wrapper = _Span(lf_span)
        try:
            yield wrapper
        except Exception as exc:
            wrapper.error(str(exc))
            raise
        finally:
            wrapper.end()

    def generation(
        self,
        name: str,
        model: str,
        input: str,
        output: str,
        latency_ms: int | None = None,
    ) -> None:
        if self._trace is None:
            return
        self._trace.generation(
            name=name,
            model=model,
            input=input,
            output=output,
            metadata={"latency_ms": latency_ms} if latency_ms else None,
        )

    def update(self, output: dict | None = None, **kwargs: Any) -> None:
        if self._trace:
            self._trace.update(output=output, **kwargs)

    def flush(self) -> None:
        if self._client:
            self._client.flush()


class LangfuseTracer:
    """
    Service-scoped Langfuse tracer.

    Instantiate once at startup (stored on app.state). Provides per-request
    trace contexts via the `trace()` context manager.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._client: Any | None = None
        cfg = settings or get_settings()

        if cfg.langfuse_enabled:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    public_key=cfg.langfuse_public_key,
                    secret_key=cfg.langfuse_secret_key,
                    host=cfg.langfuse_host,
                )
                logger.info("Langfuse tracing enabled (host=%s)", cfg.langfuse_host)
            except Exception as exc:
                logger.warning("Langfuse init failed, tracing disabled: %s", exc)
        else:
            logger.info("Langfuse tracing disabled (no credentials)")

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @contextmanager
    def trace(
        self,
        name: str,
        inputs: dict | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> Generator[_Trace, None, None]:
        """
        Context manager that creates a Langfuse trace for the duration of a block.

        Yields a _Trace object for attaching child spans and generations.
        All methods on _Trace are no-ops when Langfuse is disabled.
        """
        if self._client is None:
            yield _Trace(None, None)
            return

        lf_trace = self._client.trace(
            name=name,
            user_id=user_id,
            session_id=session_id,
            tags=tags or [],
            input=inputs or {},
        )
        wrapper = _Trace(lf_trace, self._client)
        try:
            yield wrapper
        except Exception as exc:
            lf_trace.update(output={"error": str(exc)}, level="ERROR")
            raise
        finally:
            self._client.flush()

    def shutdown(self) -> None:
        """Flush and close the Langfuse client."""
        if self._client:
            try:
                self._client.flush()
            except Exception:
                pass
