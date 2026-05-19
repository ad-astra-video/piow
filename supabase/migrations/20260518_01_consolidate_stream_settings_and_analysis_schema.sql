-- Consolidated migration for stream ownership, stream settings, and analysis schema.
BEGIN;

-- Add direct stream ownership for user-scoped stream queries.
ALTER TABLE stream_sessions
  ADD COLUMN IF NOT EXISTS user_id UUID;

-- Store stream transcription/analysis configuration in a single JSON field.
ALTER TABLE stream_sessions
  ADD COLUMN IF NOT EXISTS stream_settings JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Remove explicit setting columns in favor of stream_settings JSONB.
ALTER TABLE stream_sessions
  DROP COLUMN IF EXISTS live_transcription_enabled,
  DROP COLUMN IF EXISTS live_translation_enabled,
  DROP COLUMN IF EXISTS source_language,
  DROP COLUMN IF EXISTS target_language,
  DROP COLUMN IF EXISTS analysis_enabled,
  DROP COLUMN IF EXISTS analysis_mode,
  DROP COLUMN IF EXISTS analysis_audio_chunk_seconds,
  DROP COLUMN IF EXISTS analysis_video_chunk_seconds,
  DROP COLUMN IF EXISTS analysis_max_tokens,
  DROP COLUMN IF EXISTS analysis_video_fps,
  DROP COLUMN IF EXISTS analysis_prompt,
  DROP COLUMN IF EXISTS analysis_response_format;

-- Drop deprecated mode from persisted stream_analysis rows.
ALTER TABLE stream_analysis
  DROP COLUMN IF EXISTS analysis_mode;

-- Add normalized source type for analysis events.
ALTER TABLE stream_analysis
  ADD COLUMN IF NOT EXISTS analysis_source TEXT NOT NULL DEFAULT 'video';

ALTER TABLE stream_analysis
  DROP CONSTRAINT IF EXISTS stream_analysis_analysis_source_check;

ALTER TABLE stream_analysis
  ADD CONSTRAINT stream_analysis_analysis_source_check
  CHECK (analysis_source IN ('video', 'audio'));

-- Index for common user-scoped recent-stream queries.
CREATE INDEX IF NOT EXISTS idx_stream_sessions_user_id_created_at
  ON stream_sessions(user_id, created_at DESC);

COMMIT;
