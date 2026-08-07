"""
Unified error envelope for DocIntel Python services.

Every error response (4xx/5xx) across all services takes the shape:

    {"error": {"code": "NOT_FOUND", "message": "...", "request_id": "..."}}

Success payloads are completely unaffected — this only reshapes error
responses. Install once per FastAPI app:

    from docintel_common.errors import install_error_handlers
    install_error_handlers(app)

This registers handlers for `HTTPException` (including FastAPI's own
subclass), `RequestValidationError` (422s), and a catch-all for any
unhandled exception (mapped to 500 without leaking internals).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .tracing import TraceContext

logger = logging.getLogger(__name__)

_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def _code_for_status(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, f"HTTP_{status_code}")


def _current_request_id(request: Request) -> str:
    header_id = request.headers.get("X-Request-Id")
    if header_id:
        return header_id
    ctx_id = TraceContext.get_request_id()
    if ctx_id and ctx_id != "-":
        return ctx_id
    return str(uuid.uuid4())


def error_envelope(message: str, status_code: int, request_id: str, code: str | None = None) -> dict:
    """Build the `{"error": {...}}` body. Exposed for call sites that need to
    construct an envelope outside the standard exception-handler flow (e.g.
    inside an SSE error event, or a gateway-style proxy response)."""
    return {
        "error": {
            "code": code or _code_for_status(status_code),
            "message": message,
            "request_id": request_id,
        }
    }


def install_error_handlers(app) -> None:
    """Register the three handlers that standardize every error response."""

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        message = detail if isinstance(detail, str) else str(detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(message, exc.status_code, _current_request_id(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "Request validation failed.", 422, _current_request_id(request), code="VALIDATION_ERROR"
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=500,
            content=error_envelope("Internal server error.", 500, _current_request_id(request)),
        )
