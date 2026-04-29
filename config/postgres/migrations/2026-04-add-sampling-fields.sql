-- Migration: add LMForge sampling contract fields to admin.model_profiles
-- Run once against the live dev DB (no Flyway — still in dev).
-- Apply with: psql $POSTGRES_URL -f this_file.sql

ALTER TABLE admin.model_profiles
    ADD COLUMN IF NOT EXISTS presence_penalty             DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS repetition_penalty           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS top_k                        INTEGER,
    ADD COLUMN IF NOT EXISTS min_p                        DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS thinking_frequency_penalty   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS thinking_presence_penalty    DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS thinking_repetition_penalty  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS thinking_top_k               INTEGER,
    ADD COLUMN IF NOT EXISTS thinking_min_p               DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS thinking_budget              INTEGER;

-- Backfill Qwen3 spec defaults for all existing * wildcard profiles.
-- thinking_top_p updated to 0.95 (LMForge contract; was 0.9).
UPDATE admin.model_profiles SET
    thinking_top_p              = 0.95,
    thinking_top_k              = 20,
    thinking_min_p              = 0.0,
    thinking_frequency_penalty  = 0.0,
    thinking_presence_penalty   = 0.3,
    thinking_repetition_penalty = 1.2,
    thinking_budget             = 2048
WHERE model_pattern = '*';
