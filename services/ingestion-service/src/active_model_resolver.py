"""
ActiveModelResolver
===================
Per-tenant resolution of the **active VLM model id** at ingestion time.

Resolution chain (first non-null wins):
  1. admin.platform_settings.value   key='llm_vlm_model'  → platform-wide override
  2. admin.tenants.settings->>'llm_vlm_model'             → tenant preference
  3. env var fallback (cfg.llm_vlm_model)                 → deployment default

The same source-of-truth as admin-service's PlatformSettingsService — kept
intentionally lean here because ingestion only needs read access. We don't
add a TTL cache: the resolver is invoked once per `_run_pdf_sharded()` call
(once per document), the lookup is two cheap indexed queries, and a stale
cached value would override an admin's just-saved preference for up to
`TTL` seconds — which is the exact problem this whole feature exists to fix.

Fail-open: any DB error logs a warning and returns the env fallback so a
PostgreSQL outage cannot stop document ingestion.
"""

from __future__ import annotations

import logging
from typing import Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


def resolve_active_vlm_model(postgres_url: str, tenant_id: str, env_fallback: str) -> str:
    """Return the model id the VLM should use for `tenant_id` right now.

    `admin.tenants` has row-level security enabled — the docintel_documents
    role only sees its current tenant. We set `app.current_tenant` so the
    tenant SELECT can find the row. `admin.platform_settings` has no RLS.
    """
    try:
        with psycopg2.connect(postgres_url) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    "SELECT value FROM admin.platform_settings WHERE key = 'llm_vlm_model'"
                )
                row = cur.fetchone()
                platform = _parse_jsonb_string(row["value"] if row else None)
                if platform:
                    return platform

                cur.execute("SET LOCAL app.current_tenant = %s", (tenant_id,))
                cur.execute(
                    "SELECT settings->>'llm_vlm_model' AS m FROM admin.tenants WHERE id = %s",
                    (tenant_id,),
                )
                row = cur.fetchone()
                tenant_pref = row["m"] if row else None
                if tenant_pref:
                    return tenant_pref
    except Exception as exc:
        logger.warning(
            "VLM model resolution failed for tenant=%s — falling back to env (%s)",
            tenant_id, exc,
        )
    return env_fallback


def _parse_jsonb_string(raw) -> Optional[str]:
    """JSONB string values arrive as `None`, the literal "null", or a quoted str."""
    if raw is None:
        return None
    # psycopg2 with DictCursor returns the raw JSONB text — for a JSON string
    # it's already unquoted (e.g. "qwen2.5-vl:3b:4bit"), for JSON null it's None.
    if isinstance(raw, str):
        s = raw.strip()
        if s == "" or s == "null":
            return None
        # Defensive: if value is still wrapped in quotes (shouldn't happen with
        # ->>'key' or DictCursor but doesn't hurt), strip them.
        return s.strip('"') or None
    return None
