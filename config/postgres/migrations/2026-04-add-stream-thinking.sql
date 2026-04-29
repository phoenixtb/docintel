-- Migration: add stream_thinking flag and raise default budgets in admin.model_profiles
-- Run once against the live dev DB (no Flyway — still in dev).
-- Apply with: psql $POSTGRES_URL -f this_file.sql

ALTER TABLE admin.model_profiles
    ADD COLUMN IF NOT EXISTS stream_thinking BOOLEAN;

-- Bump defaults only for rows that still carry the old 2048/4096 values.
-- Tenants that have already customised these values are NOT touched.
UPDATE admin.model_profiles
   SET thinking_budget = 4096
 WHERE thinking_budget = 2048;

UPDATE admin.model_profiles
   SET thinking_max_tokens = 6144
 WHERE thinking_max_tokens = 4096;
