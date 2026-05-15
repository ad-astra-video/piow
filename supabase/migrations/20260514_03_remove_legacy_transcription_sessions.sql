-- ============================================================
-- Remove legacy transcription session flow schema
-- Date: 2026-05-14
--
-- The old batch/session transcription flow has been removed from
-- backend routes and code. Drop obsolete schema artifacts.
-- ============================================================

BEGIN;

DROP TABLE IF EXISTS transcription_sessions CASCADE;

ALTER TABLE user_sessions
  DROP COLUMN IF EXISTS transcription_ids;

COMMIT;
