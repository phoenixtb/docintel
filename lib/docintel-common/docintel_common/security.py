"""
Security domain models shared across DocIntel services.

Classification hierarchy (ascending clearance):
  public < internal < confidential < restricted

UserContext is built by rag-service / ingestion-service from gateway-injected headers.
DocumentACL is stored as Qdrant point payload metadata at ingestion time.
RetrievalAuditEvent is emitted as a structured log entry per RAG query.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Classification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


CLASSIFICATION_ORDER: dict[Classification, int] = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


def clearance_permits(user_clearance: Classification, doc_classification: Classification) -> bool:
    """Return True if the user's clearance level covers the document classification."""
    return CLASSIFICATION_ORDER[user_clearance] >= CLASSIFICATION_ORDER[doc_classification]


class DocumentACL(BaseModel):
    """
    Access-control metadata stored on every ingested document chunk.

    Defaults represent the baseline posture for internal documents:
    all authenticated tenant members may read, no department/region restriction.
    """

    classification: Classification = Classification.INTERNAL
    # empty list = open to all authenticated tenant members
    allowed_roles: list[str] = Field(default_factory=list)
    # explicit per-user UUID grants (bypasses role check when matched)
    allowed_users: list[str] = Field(default_factory=list)
    department: Optional[str] = None
    region: str = "global"
    # ISO8601 timestamp; None = no expiry
    expires_at: Optional[str] = None

    def to_meta(self) -> dict:
        """Serialize to a flat dict suitable for Qdrant point payload storage."""
        return {
            "classification": self.classification.value,
            "allowed_roles": self.allowed_roles,
            "allowed_users": self.allowed_users,
            "department": self.department,
            "region": self.region,
            "expires_at": self.expires_at,
        }


class UserContext(BaseModel):
    """
    Caller identity and authorization attributes, populated from gateway-forwarded headers.

    Headers set by TenantFilter (API Gateway):
      X-User-Id       → user_id (JWT sub)
      X-Tenant-Id     → tenant_id (from docintel-actions custom claim)
      X-User-Roles    → roles (comma-separated fine-grained permissions)
      X-User-Clearance→ clearance (from docintel-actions custom claim)
      X-Org-Id        → org_id (optional, falls back to tenant_id)
    """

    user_id: str
    org_id: str
    tenant_id: str
    # fine-grained permissions expanded by docintel-actions, e.g. ["documents:rw", "query:execute"]
    roles: list[str] = Field(default_factory=list)
    clearance: Classification = Classification.INTERNAL
    department: Optional[str] = None
    region: str = "global"

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def clearance_permits(self, classification: Classification) -> bool:
        return clearance_permits(self.clearance, classification)

    @classmethod
    def from_gateway_headers(cls, headers: dict) -> "UserContext":
        """
        Build UserContext from gateway-forwarded headers.

        `headers` is any dict-like that supports .get(key, default).
        Accepts both raw Header objects and plain dicts.
        """
        raw_roles = headers.get("x-user-roles") or headers.get("X-User-Roles") or ""
        roles = [r.strip() for r in raw_roles.split(",") if r.strip()] if raw_roles else []

        raw_clearance = headers.get("x-user-clearance") or headers.get("X-User-Clearance") or "internal"
        try:
            clearance = Classification(raw_clearance.lower())
        except ValueError:
            clearance = Classification.INTERNAL

        tenant_id = headers.get("x-tenant-id") or headers.get("X-Tenant-Id") or "default"
        org_id = headers.get("x-org-id") or headers.get("X-Org-Id") or tenant_id
        user_id = headers.get("x-user-id") or headers.get("X-User-Id") or ""
        department = headers.get("x-user-department") or headers.get("X-User-Department") or None
        region = headers.get("x-user-region") or headers.get("X-User-Region") or "global"

        return cls(
            user_id=user_id,
            org_id=org_id,
            tenant_id=tenant_id,
            roles=roles,
            clearance=clearance,
            department=department,
            region=region,
        )


class RetrievalAuditEvent(BaseModel):
    """Structured audit log emitted by OpaChunkValidator after every RAG retrieval."""

    request_id: str
    user_id: str
    org_id: str
    tenant_id: str
    query: str
    retrieved_chunk_ids: list[str]
    denied_chunk_ids: list[str]
    document_ids: list[str]
    timestamp: datetime
    latency_ms: int
