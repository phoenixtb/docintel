"""
Distributed trace context for DocIntel Python services.

Stores request_id / tenant_id / user_id in contextvars so every log record
emitted during a request carries the same trace fields, even across async
boundaries and thread-pool executors.

Usage
-----
Middleware (FastAPI):
    from docintel_common.tracing import TraceContext
    TraceContext.set(request_id, tenant_id, user_id)

Log formatter:
    import logging
    from docintel_common.tracing import TraceLogFilter
    handler.addFilter(TraceLogFilter())
    # format string can now use %(request_id)s, %(tenant_id)s, %(user_id)s
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_tenant_id:  ContextVar[str] = ContextVar("tenant_id",  default="-")
_user_id:    ContextVar[str] = ContextVar("user_id",    default="-")


class TraceContext:
    """Thread/coroutine-safe store for the current request's trace context."""

    @staticmethod
    def set(request_id: str, tenant_id: str, user_id: str) -> None:
        _request_id.set(request_id or str(uuid.uuid4()))
        _tenant_id.set(tenant_id or "-")
        _user_id.set(user_id or "-")

    @staticmethod
    def get_request_id() -> str:
        return _request_id.get()

    @staticmethod
    def get_tenant_id() -> str:
        return _tenant_id.get()

    @staticmethod
    def get_user_id() -> str:
        return _user_id.get()

    @staticmethod
    def as_headers() -> dict[str, str]:
        """Build headers dict for forwarding trace context to downstream services."""
        return {"X-Request-Id": _request_id.get()}


class TraceLogFilter(logging.Filter):
    """
    Injects trace context fields into every LogRecord so formatters can use them.

    Add to every handler:
        handler.addFilter(TraceLogFilter())

    Then use in format string:
        "%(asctime)s [%(request_id)s] [%(tenant_id)s] [%(user_id)s] %(levelname)-5s %(name)s - %(message)s"
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()  # type: ignore[attr-defined]
        record.tenant_id  = _tenant_id.get()   # type: ignore[attr-defined]
        record.user_id    = _user_id.get()      # type: ignore[attr-defined]
        return True


LOG_FORMAT = (
    "%(asctime)s [%(request_id)s] [%(tenant_id)s] [%(user_id)s]"
    " %(levelname)-5s %(name)s - %(message)s"
)


def configure_trace_logging(level: int = logging.INFO) -> None:
    """
    Configure root logger to emit structured records with trace fields.
    Call once at service startup, after TraceLogFilter is attached.
    """
    handler = logging.StreamHandler()
    handler.addFilter(TraceLogFilter())
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
