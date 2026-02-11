"""
Langfuse Tracing Integration
============================

Observability for RAG pipeline execution.
"""

from langfuse import Langfuse
from functools import wraps
import os
from typing import Optional, Callable
from contextlib import contextmanager


# Initialize Langfuse client
def get_langfuse() -> Optional[Langfuse]:
    """Get Langfuse client if configured."""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "http://localhost:3000")

    if not public_key or not secret_key:
        return None

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )


# Global Langfuse instance
_langfuse: Optional[Langfuse] = None


def init_langfuse():
    """Initialize global Langfuse instance."""
    global _langfuse
    _langfuse = get_langfuse()
    if _langfuse:
        print("Langfuse tracing enabled")
    else:
        print("Langfuse tracing disabled (no credentials)")


def trace_query(
    question: str,
    tenant_id: str,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
):
    """
    Decorator to trace RAG query execution.

    Usage:
        @trace_query("What is X?", "tenant_1")
        async def process():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if _langfuse is None:
                return await func(*args, **kwargs)

            trace = _langfuse.trace(
                name="rag_query",
                user_id=user_id,
                session_id=tenant_id,
                tags=tags or ["rag", "query"],
                input={"question": question},
            )

            try:
                result = await func(*args, **kwargs)
                trace.update(output=result)
                return result
            except Exception as e:
                trace.update(
                    output={"error": str(e)},
                    level="ERROR",
                )
                raise
            finally:
                _langfuse.flush()

        return wrapper

    return decorator


@contextmanager
def trace_span(
    name: str,
    metadata: Optional[dict] = None,
):
    """
    Context manager for tracing a span within a trace.

    Usage:
        with trace_span("embedding"):
            result = embedder.run(text)
    """
    if _langfuse is None:
        yield
        return

    span = None
    try:
        span = _langfuse.span(name=name, metadata=metadata)
        yield span
    except Exception as e:
        if span:
            span.update(level="ERROR", status_message=str(e))
        raise


def log_generation(
    model: str,
    prompt: str,
    completion: str,
    usage: Optional[dict] = None,
    latency_ms: Optional[int] = None,
):
    """Log an LLM generation to Langfuse."""
    if _langfuse is None:
        return

    _langfuse.generation(
        name="llm_generation",
        model=model,
        input=prompt,
        output=completion,
        usage=usage,
        metadata={"latency_ms": latency_ms} if latency_ms else None,
    )


def log_retrieval(
    query: str,
    documents: list[dict],
    scores: list[float],
):
    """Log a retrieval operation to Langfuse."""
    if _langfuse is None:
        return

    _langfuse.span(
        name="retrieval",
        input={"query": query},
        output={
            "document_count": len(documents),
            "top_score": max(scores) if scores else 0,
            "documents": documents[:3],  # Log top 3 only
        },
    )
