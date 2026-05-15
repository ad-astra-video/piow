-- ============================================================
-- Update model_used CHECK constraints for transcriptions/translations
-- Date: 2026-05-15
--
-- Move allowed model_used values from granite-4.0-1b to gemma-4-e4b
-- while retaining voxtral-realtime.
-- ============================================================

BEGIN;

ALTER TABLE transcriptions
  DROP CONSTRAINT IF EXISTS transcriptions_model_used_check;

ALTER TABLE transcriptions
  ADD CONSTRAINT transcriptions_model_used_check
  CHECK (model_used IN ('gemma-4-e4b', 'voxtral-realtime'));

ALTER TABLE translations
  DROP CONSTRAINT IF EXISTS translations_model_used_check;

ALTER TABLE translations
  ADD CONSTRAINT translations_model_used_check
  CHECK (model_used IN ('gemma-4-e4b', 'voxtral-realtime'));

COMMIT;
