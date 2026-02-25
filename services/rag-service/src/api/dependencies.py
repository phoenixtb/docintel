"""
FastAPI dependency providers.

All injectable resources live here so endpoints declare what they need
via Depends() rather than reaching into global state directly.
"""

import base64
import json
import logging
from typing import Annotated

from fastapi import Depends, Header, Request

from ..config import Settings
from ..pipelines.query import RAGService
from ..tracing import LangfuseTracer

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
# RBAC: extract tenant_id and user_roles from JWT (forwarded by API Gateway)
# ---------------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without signature verification (gateway already validated it)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        # Add padding if needed
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return {}


def extract_jwt_claims(
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    x_user_roles: Annotated[str | None, Header(alias="X-User-Roles")] = None,
) -> dict:
    """
    Extract tenant_id, user_id, user_roles from forwarded headers.

    The API Gateway validates the JWT and forwards claims as headers:
      X-Tenant-Id   → tenant_id
      X-User-Id     → user_id
      X-User-Roles  → comma-separated role list (e.g. "tenant_user,tenant_admin")

    Falls back to decoding the raw Authorization Bearer token when gateway
    headers are absent (useful for direct dev calls to the RAG service).
    """
    claims: dict = {}

    # Prefer explicit forwarded headers (set by API Gateway after JWT validation)
    if x_tenant_id:
        claims["tenant_id"] = x_tenant_id
    if x_user_id:
        claims["user_id"] = x_user_id
    if x_user_roles:
        claims["user_roles"] = [r.strip() for r in x_user_roles.split(",") if r.strip()]

    # Dev fallback: decode raw JWT if forwarded headers missing
    if authorization and authorization.startswith("Bearer ") and not claims:
        payload = _decode_jwt_payload(authorization.removeprefix("Bearer "))
        claims.setdefault("tenant_id", payload.get("tenant_id", "default"))
        claims.setdefault("user_id", payload.get("sub"))
        raw_roles = payload.get("roles", payload.get("realm_access", {}).get("roles", []))
        claims.setdefault("user_roles", raw_roles if isinstance(raw_roles, list) else [])

    # Ensure defaults
    claims.setdefault("tenant_id", "default")
    claims.setdefault("user_id", None)
    claims.setdefault("user_roles", [])

    return claims


# Type aliases for DI
SettingsDep = Annotated[Settings, Depends(get_settings)]
RAGServiceDep = Annotated[RAGService, Depends(get_rag_service)]
TracerDep = Annotated[LangfuseTracer, Depends(get_tracer)]
JWTClaimsDep = Annotated[dict, Depends(extract_jwt_claims)]
