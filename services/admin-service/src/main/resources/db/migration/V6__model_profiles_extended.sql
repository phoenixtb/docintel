-- Flyway V6: extend model_profiles with LMForge sampling fields + stream_thinking
-- Adds columns that were previously only in the manual migration scripts
-- (config/postgres/migrations/2026-04-add-sampling-fields.sql and
--  config/postgres/migrations/2026-04-add-stream-thinking.sql).
-- This makes a fresh setup fully functional without manual SQL.

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
    ADD COLUMN IF NOT EXISTS thinking_budget              INTEGER,
    ADD COLUMN IF NOT EXISTS stream_thinking              BOOLEAN;

-- Backfill wildcard profiles: set stream_thinking=true and raise budgets only
-- where still at the old defaults (so tenant customisations are not clobbered).
UPDATE admin.model_profiles SET
    stream_thinking = true
WHERE model_pattern = '*'
  AND stream_thinking IS NULL;

UPDATE admin.model_profiles SET
    thinking_budget = 4096
WHERE model_pattern = '*'
  AND thinking_budget = 2048;

UPDATE admin.model_profiles SET
    thinking_max_tokens = 6144
WHERE model_pattern = '*'
  AND thinking_max_tokens = 4096;

UPDATE admin.model_profiles SET
    thinking_repetition_penalty = 1.2
WHERE model_pattern = '*'
  AND (thinking_repetition_penalty IS NULL OR thinking_repetition_penalty = 1.1);
