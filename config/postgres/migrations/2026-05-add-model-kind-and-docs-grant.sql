-- Migration: add `kind` informational column to admin.model_profiles
--            and grant docintel_documents SELECT on admin.model_profiles
--            (ingestion-service now reads VLM sampling params via the resolver).
-- Run once against the live dev DB (no Flyway — still in dev).
-- Apply with: psql $POSTGRES_URL -f this_file.sql

ALTER TABLE admin.model_profiles
    ADD COLUMN IF NOT EXISTS kind VARCHAR(16);

-- kind is informational only (used by UI to badge / hide irrelevant fields).
-- NULL means "auto-infer from model_pattern at read time" — see infer_kind() in
-- docintel_common.model_profile_resolver.
COMMENT ON COLUMN admin.model_profiles.kind IS
    'Informational: chat | vlm | embed | rerank. NULL = auto-infer from model_pattern.';

-- Backfill auto-inferred values where pattern hints unambiguously.
UPDATE admin.model_profiles
   SET kind = 'rerank'
 WHERE kind IS NULL AND lower(model_pattern) LIKE '%rerank%';

UPDATE admin.model_profiles
   SET kind = 'embed'
 WHERE kind IS NULL AND lower(model_pattern) LIKE '%embed%';

UPDATE admin.model_profiles
   SET kind = 'vlm'
 WHERE kind IS NULL
   AND (lower(model_pattern) LIKE '%-vl%'
        OR lower(model_pattern) LIKE '%vision%'
        OR lower(model_pattern) LIKE 'vl%');

-- Wildcard '*' rows stay NULL (intentionally — they apply to every kind).

-- Grant ingestion-service's role read access so its in-process resolver can
-- fetch tenant + platform profiles for VLM model sampling params.
GRANT SELECT ON admin.model_profiles TO docintel_documents;
