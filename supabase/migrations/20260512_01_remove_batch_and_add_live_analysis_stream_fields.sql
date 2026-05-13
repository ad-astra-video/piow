-- Remove batch transcription/translation storage paths and add live analysis stream config fields.

BEGIN;

-- Remove batch-only session tracking table.
DROP TABLE IF EXISTS transcription_sessions CASCADE;

-- Remove batch-origin transcription data so stream-only constraints can be enforced.
DELETE FROM transcriptions
WHERE source_type IS NULL OR source_type NOT IN ('stream', 'whip');

DELETE FROM transcription_usage
WHERE source_type IS NULL OR source_type NOT IN ('stream', 'whip');

-- Tighten source_type values to stream-only modes.
ALTER TABLE transcriptions
  DROP CONSTRAINT IF EXISTS transcriptions_source_type_check;
ALTER TABLE transcriptions
  ADD CONSTRAINT transcriptions_source_type_check
  CHECK (source_type IN ('stream', 'whip'));

ALTER TABLE transcription_usage
  DROP CONSTRAINT IF EXISTS transcription_usage_source_type_check;
ALTER TABLE transcription_usage
  ADD CONSTRAINT transcription_usage_source_type_check
  CHECK (source_type IN ('stream', 'whip'));

-- Add live analysis fields to stream sessions.
ALTER TABLE stream_sessions
  ADD COLUMN IF NOT EXISTS analysis_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS analysis_mode TEXT NOT NULL DEFAULT 'multimodal',
  ADD COLUMN IF NOT EXISTS analysis_audio_chunk_seconds NUMERIC(6,3) NOT NULL DEFAULT 1.0,
  ADD COLUMN IF NOT EXISTS analysis_video_fps INTEGER NOT NULL DEFAULT 3;

ALTER TABLE stream_sessions
  DROP CONSTRAINT IF EXISTS stream_sessions_analysis_mode_check;
ALTER TABLE stream_sessions
  ADD CONSTRAINT stream_sessions_analysis_mode_check
  CHECK (analysis_mode IN ('multimodal', 'audio_only', 'video_only'));

COMMIT;
