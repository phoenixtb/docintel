#!/usr/bin/env python3
"""
Generate the aggregated public OpenAPI spec (docs/api/openapi.json) for DocIntel.

Each service's OpenAPI document describes its *internal* routes (the paths it
actually serves). The gateway (services/api-gateway/src/main/resources/application.yml)
rewrites public `/api/v1/...` paths to those internal paths. This script:

  1. Exports the raw internal spec from each service (FastAPI services via
     `uv run python -c "..."`, Kotlin services via `/v3/api-docs` over the
     docker network since they have no host port mapping).
  2. Maps each internal path to its public `/api/v1/...` path using an
     explicit per-service table mirroring the gateway's RewritePath filters.
  3. Drops paths the gateway does not route (internal-only surface).
  4. Merges everything into one OpenAPI 3.1 document with a single server
     entry (the gateway) and writes it to docs/api/openapi.json.

The path tables below are hand-derived from application.yml and must be kept
in sync with it; there's no way to parse Spring's RewritePath regexes
generically, so this is intentionally explicit rather than clever.

Raw per-service exports are cached under docs/api/raw/ so the merge can be
re-run offline; a live re-export requires the docker compose stack to be up
for the two Kotlin services (FastAPI services never require the stack).

Usage:
    python3 scripts/generate-openapi.py [--skip-kotlin] [--out docs/api/openapi.json]

Regeneration instructions:
    docker compose up -d document-service admin-service
    python3 scripts/generate-openapi.py
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "docs" / "api" / "raw"
GATEWAY_STATIC_COPY = REPO_ROOT / "services" / "api-gateway" / "src" / "main" / "resources" / "static" / "openapi.json"

GATEWAY_SERVERS = [
    {"url": "http://localhost:8080", "description": "API gateway (local/dev)"},
]

# Explicit raw-path -> public-path tables, one per service. Every path the
# gateway routes publicly must have an entry; anything absent is treated as
# internal-only and dropped from the aggregated spec.
PATH_MAP_DOCUMENT_SERVICE = {
    "/internal/documents": "/api/v1/documents",
    "/internal/documents/{id}": "/api/v1/documents/{id}",
    "/internal/documents/all": "/api/v1/documents/all",
    "/internal/documents/stats": "/api/v1/documents/stats",
    "/internal/documents/events": "/api/v1/documents/events",
    "/internal/documents/cleanup/preview": "/api/v1/documents/cleanup/preview",
    "/internal/documents/cleanup/jobs": "/api/v1/documents/cleanup/jobs",
    "/internal/documents/cleanup/jobs/{jobId}": "/api/v1/documents/cleanup/jobs/{jobId}",
    "/internal/documents/cleanup/jobs/{jobId}/events": "/api/v1/documents/cleanup/jobs/{jobId}/events",
}

PATH_MAP_ADMIN_SERVICE = {
    "/internal/users/me/preferences": "/api/v1/users/me/preferences",
    "/internal/tenants": "/api/v1/tenants",
    "/internal/tenants/{tenantId}": "/api/v1/tenants/{tenantId}",
    "/internal/tenants/{tenantId}/settings": "/api/v1/tenants/{tenantId}/settings",
    "/internal/tenants/{tenantId}/model-profiles": "/api/v1/tenants/{tenantId}/model-profiles",
    "/internal/tenants/{tenantId}/model-profiles/{id}": "/api/v1/tenants/{tenantId}/model-profiles/{id}",
    "/internal/tenants/{tenantId}/model-profiles/seed": "/api/v1/tenants/{tenantId}/model-profiles/seed",
    "/internal/tenants/{tenantId}/usage": "/api/v1/tenants/{tenantId}/usage",
    "/internal/tenants/{tenantId}/users": "/api/v1/tenants/{tenantId}/users",
    "/internal/tenants/{tenantId}/users/{userId}/role": "/api/v1/tenants/{tenantId}/users/{userId}/role",
    "/internal/active-models": "/api/v1/admin/active-models",
    "/internal/model-profiles": "/api/v1/admin/model-profiles",
    "/internal/model-profiles/{id}": "/api/v1/admin/model-profiles/{id}",
    "/internal/platform/settings": "/api/v1/admin/platform/settings",
    "/internal/stats": "/api/v1/admin/stats",
    "/internal/cache/stats": "/api/v1/admin/cache/stats",
    "/internal/cache/clear": "/api/v1/admin/cache/clear",
    "/internal/cache/clear/{tenantId}": "/api/v1/admin/cache/clear/{tenantId}",
    # /internal/health is the actuator-style internal probe, not part of the
    # public admin surface — intentionally left unmapped (dropped).
}

PATH_MAP_RAG_SERVICE = {
    "/query": "/api/v1/query",
    "/query/stream": "/api/v1/query/stream",
    "/conversations": "/api/v1/conversations",
    "/conversations/{conversation_id}": "/api/v1/conversations/{conversation_id}",
    "/vector-stats": "/api/v1/vector-stats",
    "/models": "/api/v1/models",
    "/internal/settings-cache/{tenant_id}": "/api/v1/tenants/{tenant_id}/settings/invalidate-cache",
    "/internal/user-settings-cache": "/api/v1/users/me/preferences/invalidate-cache",
    "/internal/model-profiles-cache": "/api/v1/admin/model-profiles-cache",
    "/internal/model-profiles-cache/{tenant_id}": "/api/v1/tenants/{tenant_id}/model-profiles-cache",
    "/internal/model-profiles/resolve/{tenant_id}": "/api/v1/tenants/{tenant_id}/model-profiles/resolve",
}

PATH_MAP_INGESTION_SERVICE = {
    "/ingest": "/api/v1/ingest",
}

PATH_MAP_DATA_LOADER = {
    "/datasets": "/api/v1/datasets",
    "/datasets/load": "/api/v1/datasets/load",
    "/datasets/load/{job_id}/progress": "/api/v1/datasets/load/{job_id}/progress",
}

PATH_MAP_ANALYTICS_SERVICE = {
    "/events/feedback": "/api/v1/feedback",
    "/analytics/feedback": "/api/v1/analytics/feedback",
    "/analytics/feedback/summary": "/api/v1/analytics/feedback/summary",
    "/analytics/feedback/timeseries": "/api/v1/analytics/feedback/timeseries",
    "/analytics/quality": "/api/v1/analytics/quality",
    "/analytics/queries/by-model": "/api/v1/analytics/queries/by-model",
    "/analytics/queries/summary": "/api/v1/analytics/queries/summary",
    "/analytics/queries/timeseries": "/api/v1/analytics/queries/timeseries",
    "/analytics/top-queries": "/api/v1/analytics/top-queries",
    "/analytics/usage": "/api/v1/analytics/usage",
}

SERVICES = [
    {
        "name": "document-service",
        "kind": "kotlin",
        "container": "document-service",
        "port": 8081,
        "path_map": PATH_MAP_DOCUMENT_SERVICE,
    },
    {
        "name": "admin-service",
        "kind": "kotlin",
        "container": "admin-service",
        "port": 8082,
        "path_map": PATH_MAP_ADMIN_SERVICE,
    },
    {
        "name": "rag-service",
        "kind": "python",
        "service_dir": "rag-service",
        "import_path": "src.api.main",
        "path_map": PATH_MAP_RAG_SERVICE,
    },
    {
        "name": "ingestion-service",
        "kind": "python",
        "service_dir": "ingestion-service",
        "import_path": "src.api.main",
        "path_map": PATH_MAP_INGESTION_SERVICE,
    },
    {
        "name": "data-loader",
        "kind": "python",
        "service_dir": "data-loader",
        "import_path": "src.api.main",
        "path_map": PATH_MAP_DATA_LOADER,
    },
    {
        "name": "analytics-service-py",
        "kind": "python",
        "service_dir": "analytics-service-py",
        "import_path": "src.main",
        "path_map": PATH_MAP_ANALYTICS_SERVICE,
    },
]


def export_python_spec(svc: dict) -> dict:
    service_dir = REPO_ROOT / "services" / svc["service_dir"]
    code = (
        "import json\n"
        f"from {svc['import_path']} import app\n"
        "print(json.dumps(app.openapi()))\n"
    )
    result = subprocess.run(
        ["uv", "run", "python", "-c", code],
        cwd=service_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def export_kotlin_spec(svc: dict) -> dict:
    container = svc["container"]
    port = svc["port"]
    remote_path = f"/tmp/{svc['name']}-openapi.json"
    subprocess.run(
        ["docker", "compose", "exec", "-T", container, "wget", "-qO", remote_path,
         f"http://localhost:{port}/v3/api-docs"],
        cwd=REPO_ROOT,
        check=True,
    )
    local_path = RAW_DIR / f"{svc['name']}.json"
    subprocess.run(
        ["docker", "compose", "cp", f"{container}:{remote_path}", str(local_path)],
        cwd=REPO_ROOT,
        check=True,
    )
    return json.loads(local_path.read_text())


def load_cached(svc: dict) -> dict:
    local_path = RAW_DIR / f"{svc['name']}.json"
    if not local_path.exists():
        raise FileNotFoundError(
            f"No cached raw spec for {svc['name']} and live export failed/skipped. "
            f"Bring up the service and re-run without --skip-kotlin, or run once "
            f"with the stack up to populate {local_path}."
        )
    return json.loads(local_path.read_text())


def fetch_service_spec(svc: dict, skip_kotlin: bool) -> dict:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if svc["kind"] == "python":
        spec = export_python_spec(svc)
        (RAW_DIR / f"{svc['name']}.json").write_text(json.dumps(spec, indent=2) + "\n")
        return spec
    if skip_kotlin:
        return load_cached(svc)
    try:
        return export_kotlin_spec(svc)
    except subprocess.CalledProcessError as exc:
        print(f"warning: live export failed for {svc['name']} ({exc}); using cached raw spec", file=sys.stderr)
        return load_cached(svc)


def build_public_paths(svc: dict, raw_spec: dict) -> tuple[dict, list[str]]:
    public_paths = {}
    dropped = []
    path_map = svc["path_map"]
    for raw_path, item in raw_spec.get("paths", {}).items():
        public_path = path_map.get(raw_path)
        if public_path is None:
            dropped.append(raw_path)
            continue
        public_paths[public_path] = item
    unmapped_entries = set(path_map) - set(raw_spec.get("paths", {}))
    if unmapped_entries:
        print(f"warning: {svc['name']} path_map has stale entries no longer in the live spec: "
              f"{sorted(unmapped_entries)}", file=sys.stderr)
    return public_paths, dropped


ERROR_SCHEMA = {
    "type": "object",
    "description": "Standard DocIntel error envelope (G7.3), returned by every service and by "
                    "the gateway itself (401/403/429/503) for any non-2xx response.",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "example": "NOT_FOUND"},
                "message": {"type": "string", "example": "Document not found."},
                "request_id": {"type": "string", "example": "b3f1c2..."},
            },
            "required": ["code", "message"],
        }
    },
    "required": ["error"],
}


def annotate_error_responses(paths: dict) -> None:
    """Document the shared error envelope as the default response on every
    operation that doesn't already declare one, without touching existing
    per-status responses."""
    for item in paths.values():
        for key, operation in item.items():
            if key not in ("get", "post", "put", "patch", "delete", "options", "head"):
                continue
            responses = operation.setdefault("responses", {})
            responses.setdefault("default", {
                "description": "Error (see the DocIntel error envelope)",
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
            })


def merge_schemas(all_specs: list[tuple[dict, dict]]) -> dict:
    merged: dict = {}
    for svc, spec in all_specs:
        schemas = spec.get("components", {}).get("schemas", {})
        for schema_name, schema in schemas.items():
            if schema_name in merged and merged[schema_name] != schema:
                schema_name = f"{svc['name'].replace('-', '_')}_{schema_name}"
            merged[schema_name] = schema
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-kotlin", action="store_true",
                         help="Reuse cached docs/api/raw/*.json instead of hitting the running containers")
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "api" / "openapi.json"))
    args = parser.parse_args()

    merged_paths: dict = {}
    all_specs = []
    all_dropped: dict[str, list[str]] = {}

    for svc in SERVICES:
        print(f"exporting {svc['name']} ({svc['kind']})...", file=sys.stderr)
        raw_spec = fetch_service_spec(svc, args.skip_kotlin)
        public_paths, dropped = build_public_paths(svc, raw_spec)
        all_dropped[svc["name"]] = dropped
        for path, item in public_paths.items():
            if path in merged_paths:
                print(f"warning: duplicate public path {path} from {svc['name']} overwrites previous entry", file=sys.stderr)
            merged_paths[path] = item
        all_specs.append((svc, raw_spec))

    merged_schemas = merge_schemas(all_specs)
    merged_schemas["Error"] = ERROR_SCHEMA
    annotate_error_responses(merged_paths)

    merged = {
        "openapi": "3.1.0",
        "info": {
            "title": "DocIntel Public API",
            "description": (
                "Aggregated public surface of the DocIntel platform, as exposed through the "
                "api-gateway. Generated from each service's OpenAPI export by "
                "scripts/generate-openapi.py — do not hand-edit; regenerate instead."
            ),
            "version": "1.0.0",
        },
        "servers": GATEWAY_SERVERS,
        "paths": dict(sorted(merged_paths.items())),
        "components": {
            "schemas": merged_schemas,
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                }
            },
        },
        "security": [{"bearerAuth": []}],
    }

    out_path = Path(args.out)
    serialized = json.dumps(merged, indent=2) + "\n"
    out_path.write_text(serialized)
    GATEWAY_STATIC_COPY.parent.mkdir(parents=True, exist_ok=True)
    GATEWAY_STATIC_COPY.write_text(serialized)
    print(f"wrote {out_path} ({len(merged_paths)} public paths, {len(merged_schemas)} schemas)", file=sys.stderr)
    print(f"wrote gateway static copy {GATEWAY_STATIC_COPY}", file=sys.stderr)

    for name, dropped in all_dropped.items():
        if dropped:
            print(f"  {name}: {len(dropped)} internal-only path(s) excluded: {sorted(dropped)}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
