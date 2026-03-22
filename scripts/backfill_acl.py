#!/usr/bin/env python3
"""
Backfill default ACL metadata for existing Qdrant document points.

Scans all ``documents_*`` collections and sets default ACL fields on any point
that has null / missing classification metadata. Run once after deploying
the ABAC ingestion changes to bring existing data up to the new schema.

Usage:
    python scripts/backfill_acl.py [--qdrant-url http://localhost:6333] [--dry-run]
"""

import argparse
import logging
import sys

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_acl")

DEFAULT_ACL_PAYLOAD = {
    "classification": "internal",
    "allowed_roles": [],
    "allowed_users": [],
    "department": None,
    "region": "global",
    "expires_at": None,
}


def backfill_collection(client: QdrantClient, collection: str, dry_run: bool) -> int:
    """Backfill ACL defaults for points missing classification. Returns count updated."""
    log.info("Scanning collection: %s", collection)
    updated = 0
    offset = None

    while True:
        scroll_result = client.scroll(
            collection_name=collection,
            scroll_filter=qmodels.Filter(
                should=[
                    qmodels.IsNullCondition(
                        is_null=qmodels.PayloadField(key="meta.classification")
                    ),
                    qmodels.IsEmptyCondition(
                        is_empty=qmodels.PayloadField(key="meta.classification")
                    ),
                ]
            ),
            limit=500,
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )

        points, next_offset = scroll_result

        if not points:
            break

        batch_ids = [p.id for p in points]
        log.info("  Found %d points without classification", len(batch_ids))

        if not dry_run:
            client.set_payload(
                collection_name=collection,
                payload={f"meta.{k}": v for k, v in DEFAULT_ACL_PAYLOAD.items()},
                points=batch_ids,
            )

        updated += len(batch_ids)

        if next_offset is None:
            break
        offset = next_offset

    return updated


def main():
    parser = argparse.ArgumentParser(description="Backfill ACL metadata in Qdrant")
    parser.add_argument(
        "--qdrant-url",
        default="http://localhost:6333",
        help="Qdrant URL (default: http://localhost:6333)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report without writing changes",
    )
    args = parser.parse_args()

    client = QdrantClient(url=args.qdrant_url)

    collections = [
        c.name
        for c in client.get_collections().collections
        if c.name.startswith("documents_")
    ]

    if not collections:
        log.info("No document collections found — nothing to backfill.")
        sys.exit(0)

    log.info("Collections to process: %s", collections)
    if args.dry_run:
        log.info("DRY RUN — no changes will be written")

    total = 0
    for collection in collections:
        count = backfill_collection(client, collection, args.dry_run)
        log.info("  %s: %d points %s", collection, count, "would be updated" if args.dry_run else "updated")
        total += count

    log.info("Done. Total points %s: %d", "to update" if args.dry_run else "updated", total)


if __name__ == "__main__":
    main()
