"""
FastAPI dependency providers.

All injectable resources live here so endpoints declare what they need
via Depends() rather than reaching into global state directly.
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Request

from docintel_common.internal_auth import verify_internal_token
from docintel_common.security import UserContext

from ..config import Settings
from ..pipelines.query import RAGService
from ..tracing import LangfuseTracer
from ..utils.asyncio import _run_db
from .schemas import QueryRequest

logger = logging.getLogger(__name__)


def get_settings(request: Request) -> Settings:
    """Return the Settings instance stored on app.state."""
    return request.app.state.settings


def get_rag_service(request: Request) -> RAGService:
    """Return the RAGService instance stored on app.state."""
    return request.app.state.rag_service


def get_tracer(request: Request) -> LangfuseTracer:
    """Return the LangfuseTracer instance stored on app.state."""
    return request.app.state.tracer


# ---------------------------------------------------------------------------
# Inter-service authentication + claim extraction
# ---------------------------------------------------------------------------

def require_internal_token(
    request: Request,
    x_internal_service_token: Annotated[str | None, Header(alias="X-Internal-Service-Token")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-Id")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> None:
    """
    Verify X-Internal-Service-Token HMAC before trusting any X-* headers.

    The API Gateway computes HMAC-SHA256("{requestId}:{tenantId}:{userId}", secret)
    and attaches it as X-Internal-Service-Token. Any request without a valid token
    bypassed the gateway — reject with 403.
    """
    settings: Settings = request.app.state.settings
    secret = settings.internal_gateway_secret

    if not secret:
        # Secret not configured — service misconfiguration, fail closed.
        logger.error("INTERNAL_GATEWAY_SECRET is not set; rejecting all requests.")
        raise HTTPException(status_code=403, detail="Service misconfiguration.")

    token = x_internal_service_token or ""
    if not verify_internal_token(
        token=token,
        request_id=x_request_id or "",
        tenant_id=x_tenant_id or "",
        user_id=x_user_id or "",
        secret=secret,
    ):
        logger.warning(
            "Invalid X-Internal-Service-Token — request may have bypassed the gateway "
            "(request_id=%s, tenant=%s, user=%s)",
            x_request_id, x_tenant_id, x_user_id,
        )
        raise HTTPException(
            status_code=403,
            detail="Missing or invalid internal service token. All requests must pass through the API Gateway.",
        )


def extract_user_context(
    request: Request,
    _: Annotated[None, Depends(require_internal_token)],
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_user_roles: Annotated[str | None, Header(alias="X-User-Roles")] = None,
    x_user_clearance: Annotated[str | None, Header(alias="X-User-Clearance")] = None,
    x_org_id: Annotated[str | None, Header(alias="X-Org-Id")] = None,
    x_user_department: Annotated[str | None, Header(alias="X-User-Department")] = None,
    x_user_region: Annotated[str | None, Header(alias="X-User-Region")] = None,
) -> UserContext:
    """
    Build UserContext from gateway-forwarded headers.

    Requires a valid X-Internal-Service-Token (enforced via require_internal_token).
    The JWT is NOT re-decoded — the gateway is the sole JWT validation point.
    """
    headers = {
        "X-Tenant-Id": x_tenant_id or "default",
        "X-User-Id": x_user_id or "",
        "X-User-Roles": x_user_roles or "",
        "X-User-Clearance": x_user_clearance or "internal",
        "X-Org-Id": x_org_id or x_tenant_id or "default",
        "X-User-Department": x_user_department,
        "X-User-Region": x_user_region or "global",
    }
    return UserContext.from_gateway_headers(headers)


# Type aliases for DI
SettingsDep = Annotated[Settings, Depends(get_settings)]
RAGServiceDep = Annotated[RAGService, Depends(get_rag_service)]
TracerDep = Annotated[LangfuseTracer, Depends(get_tracer)]
UserContextDep = Annotated[UserContext, Depends(extract_user_context)]


# ---------------------------------------------------------------------------
# Conversation history dependency
# ---------------------------------------------------------------------------

@dataclass
class LoadedHistory:
    messages: list[dict] = field(default_factory=list)
    context_state: dict = field(default_factory=dict)


async def get_conversation_history(
    request: QueryRequest,
    rag_service: RAGServiceDep,
    user_ctx: UserContextDep,
) -> LoadedHistory:
    """
    Load conversation history and context_state for the current request.

    Returns an empty LoadedHistory when no conversation_id is provided.
    DB errors are propagated as 500 HTTPException so the caller can handle them.
    """
    if not request.conversation_id:
        return LoadedHistory()
    try:
        messages, context_state = await _run_db(
            lambda: rag_service._load_conversation_history(
                request.conversation_id, user_ctx.tenant_id
            )
        )
        return LoadedHistory(messages=messages, context_state=context_state)
    except Exception as e:
        logger.error("Failed to load conversation history: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load conversation history")


ConversationHistoryDep = Annotated[LoadedHistory, Depends(get_conversation_history)]
