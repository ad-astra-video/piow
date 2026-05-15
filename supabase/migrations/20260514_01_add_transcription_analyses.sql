-- ============================================================
-- Persist live analysis summaries for streams
-- Date: 2026-05-14
--
-- Adds stream-first analysis summaries so History can render
-- an intuitive unified view for streams with live analysis enabled.
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS stream_analysis (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  stream_session_id UUID NOT NULL REFERENCES stream_sessions(id) ON DELETE CASCADE,
  analysis_mode TEXT NOT NULL CHECK (analysis_mode IN ('multimodal', 'audio_only', 'video_only')),
  summary_text TEXT NOT NULL,
  source_event_type TEXT NOT NULL DEFAULT 'analysis.done',
  timestamp_ms INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stream_analysis_stream_session_id
  ON stream_analysis(stream_session_id);

CREATE INDEX IF NOT EXISTS idx_stream_analysis_user_created_at
  ON stream_analysis(user_id, created_at DESC);

ALTER TABLE stream_analysis ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can manage stream_analysis" ON stream_analysis;
DROP POLICY IF EXISTS "Users can view own stream_analysis" ON stream_analysis;
DROP POLICY IF EXISTS "Users can insert own stream_analysis" ON stream_analysis;

CREATE POLICY "Service role can manage stream_analysis" ON stream_analysis
  FOR ALL
  USING ((auth.jwt() ->> 'role') = 'service_role');

CREATE POLICY "Users can view own stream_analysis" ON stream_analysis
  FOR SELECT
  USING (user_id = auth.uid());

CREATE POLICY "Users can insert own stream_analysis" ON stream_analysis
  FOR INSERT
  WITH CHECK (user_id = auth.uid());

COMMIT;
