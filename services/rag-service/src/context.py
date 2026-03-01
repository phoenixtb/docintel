"""
Async context variables for tenant isolation.

Set in FastAPI middleware at request ingress; read by db.py RLS listener and
any code that doesn't use FastAPI Depends().
"""
from contextvars import ContextVar

_tenant_ctx: ContextVar[str] = ContextVar("tenant_id", default="default")
_role_ctx:   ContextVar[str] = ContextVar("user_role",  default="tenant_user")
