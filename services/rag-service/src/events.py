"""
Typed Pipeline Events
=====================

Frozen dataclasses representing every event that RAGService.stream() can yield.
Serialisation is the caller's responsibility (see _serialize_sse in main.py).

Event order in a normal stream:
  MetadataEvent(cache_hit=False)
  RoutingEvent(domain=..., explicit=...)
  [QueuedEvent — only when semaphore is saturated]
  ThinkingTokenEvent* (only when effective_thinking=True)
  [StatusEvent(stage="generating_answer") — only when LMForge Call 2 prefill begins]
  TokenEvent+
  SourcesEvent(done=True)

Cache-hit short-circuit:
  MetadataEvent(cache_hit=False)
  MetadataEvent(cache_hit=True)
  TokenEvent+ (typewriter replay)
  SourcesEvent(done=True)

No-docs path:
  MetadataEvent(cache_hit=False)
  RoutingEvent(...)
  TokenEvent (single no-docs message)
  SourcesEvent(sources=[], done=True)

Error path:
  MetadataEvent(cache_hit=False)
  ErrorEvent(message=...)
"""

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class MetadataEvent:
    query_id: str
    cache_hit: bool
    context_state: Optional[dict] = None
    reranker_degraded: Optional[bool] = None


@dataclass(frozen=True)
class RoutingEvent:
    domain: Optional[str]
    explicit: bool
    confidence: Optional[float] = None


@dataclass(frozen=True)
class QueuedEvent:
    message: str = "Processing your request — a moment please..."


@dataclass(frozen=True)
class ThinkingTokenEvent:
    text: str


@dataclass(frozen=True)
class TokenEvent:
    text: str


@dataclass(frozen=True)
class SourcesEvent:
    sources: list
    done: bool = True


@dataclass(frozen=True)
class ErrorEvent:
    message: str


@dataclass(frozen=True)
class StatusEvent:
    """UX-only lifecycle signal — not persisted to conversation history.

    stage values:
      "generating_answer" — LMForge began Call 2 prefill (thinking budget exhausted).
                            The engine is re-processing the reasoning block; no tokens
                            will arrive for several seconds. The UI should show an
                            indicator so users know the request is still active.
    """
    stage: str


# Union type alias used for type hints throughout the codebase.
PipelineEvent = Union[
    MetadataEvent,
    RoutingEvent,
    QueuedEvent,
    ThinkingTokenEvent,
    StatusEvent,
    TokenEvent,
    SourcesEvent,
    ErrorEvent,
]

__all__ = [
    "MetadataEvent",
    "RoutingEvent",
    "QueuedEvent",
    "ThinkingTokenEvent",
    "StatusEvent",
    "TokenEvent",
    "SourcesEvent",
    "ErrorEvent",
    "PipelineEvent",
]
