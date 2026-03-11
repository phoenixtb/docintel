"""
FastAPI dependency providers.

All injectable resources live here so endpoints declare what they need
via Depends() rather than reaching into global state directly.
"""

import base64
import json
import logging
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

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
# JWT claim extraction
# ---------------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without signature verification (gateway already validated it)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def extract_jwt_claims(
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> dict:
    """
    Extract tenant_id, user_id, user_roles from the forwarded JWT.

    The API Gateway validates the JWT signature and forwards:
      - The raw Authorization header (for claim extraction)
      - X-Tenant-Id, X-User-Id, X-User-Role headers (extracted from JWT)

    Defense-in-depth: reject any request that did not come through the gateway
    (i.e., has neither a JWT nor the gateway-injected X-User-Id header).

    Authentik claim layout (from docintel-setup.yaml scope mappings):
      sub        → user UUID
      tenant_id  → from 'tenant' scope mapping  (top-level claim)
      role       → from 'role'   scope mapping  (top-level, singular string)
    """
    claims: dict = {"tenant_id": "default", "user_id": None, "user_roles": []}

    # Primary: decode the JWT — authoritative source for all identity claims
    if authorization and authorization.startswith("Bearer "):
        payload = _decode_jwt_payload(authorization.removeprefix("Bearer "))
        if payload:
            tenant_id = payload.get("tenant_id")
            if not tenant_id:
                nested = payload.get("tenant", {})
                tenant_id = nested.get("tenant_id") if isinstance(nested, dict) else None
            if tenant_id:
                claims["tenant_id"] = tenant_id

            claims["user_id"] = payload.get("sub")

            role = payload.get("role")
            if role:
                claims["user_roles"] = [role] if isinstance(role, str) else list(role)

            return claims

    # Fallback: gateway-forwarded headers (direct service calls in integration tests/dev)
    if x_user_id:
        claims["user_id"] = x_user_id
        if x_tenant_id:
            claims["tenant_id"] = x_tenant_id
        return claims

    # No JWT and no gateway headers — reject. This request bypassed the gateway.
    raise HTTPException(
        status_code=403,
        detail="Missing authentication credentials. All requests must pass through the API Gateway.",
    )


# Type aliases for DI
SettingsDep = Annotated[Settings, Depends(get_settings)]
RAGServiceDep = Annotated[RAGService, Depends(get_rag_service)]
TracerDep = Annotated[LangfuseTracer, Depends(get_tracer)]
JWTClaimsDep = Annotated[dict, Depends(extract_jwt_claims)]
