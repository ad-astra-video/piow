-- ============================================================
-- Re-key stream-derived tables to stream_sessions
-- Date: 2026-05-14
--
-- Stream-derived rows should be keyed by stream_sessions, not
-- transcriptions. This migration updates:
--   - transcriptions: add stream_session_id for linkage
--   - transcription_sentences: key by stream_session_id
--   - translations: key by stream_session_id
--   - stream_analysis: ensure stream_session_id naming/shape
-- ============================================================

BEGIN;

-- Keep a direct stream_session pointer on transcription headers.
ALTER TABLE transcriptions
  ADD COLUMN IF NOT EXISTS stream_session_id UUID REFERENCES stream_sessions(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_transcriptions_stream_session_id
  ON transcriptions(stream_session_id);

-- Best-effort backfill from stream://<uuid> audio_url.
UPDATE transcriptions
SET stream_session_id = NULLIF(substring(audio_url from 'stream://([0-9a-fA-F-]{36})'), '')::uuid
WHERE stream_session_id IS NULL
  AND audio_url LIKE 'stream://%';

-- -----------------------------
-- stream_analysis normalization
-- -----------------------------
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'stream_analysis'
      AND column_name = 'stream_id'
  ) THEN
    ALTER TABLE stream_analysis RENAME COLUMN stream_id TO stream_session_id;
  END IF;
END
$$;

ALTER TABLE stream_analysis
  ALTER COLUMN stream_session_id SET NOT NULL;

ALTER TABLE stream_analysis
  DROP COLUMN IF EXISTS transcription_id;

DROP INDEX IF EXISTS idx_stream_analysis_stream_id;
DROP INDEX IF EXISTS idx_stream_analysis_transcription_id;
DROP INDEX IF EXISTS idx_stream_analysis_transcription_created_at;

CREATE INDEX IF NOT EXISTS idx_stream_analysis_stream_session_id
  ON stream_analysis(stream_session_id);

-- --------------------------------------
-- transcription_sentences: stream keyed
-- --------------------------------------
ALTER TABLE transcription_sentences
  ADD COLUMN IF NOT EXISTS stream_session_id UUID REFERENCES stream_sessions(id) ON DELETE CASCADE;

UPDATE transcription_sentences ts
SET stream_session_id = t.stream_session_id
FROM transcriptions t
WHERE ts.transcription_id = t.id
  AND ts.stream_session_id IS NULL;

ALTER TABLE transcription_sentences
  ALTER COLUMN stream_session_id SET NOT NULL;

ALTER TABLE transcription_sentences
  DROP CONSTRAINT IF EXISTS transcription_sentences_transcription_id_sentence_index_key;

DROP INDEX IF EXISTS idx_transcription_sentences_transcription_id;
DROP INDEX IF EXISTS idx_transcription_sentences_lookup;

CREATE INDEX IF NOT EXISTS idx_transcription_sentences_stream_session_id
  ON transcription_sentences(stream_session_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_transcription_sentences_stream_session_sentence_index
  ON transcription_sentences(stream_session_id, sentence_index);

DROP POLICY IF EXISTS "Users can view own transcription_sentences" ON transcription_sentences;
DROP POLICY IF EXISTS "Users can insert own transcription_sentences" ON transcription_sentences;
DROP POLICY IF EXISTS "Users can update own transcription_sentences" ON transcription_sentences;

ALTER TABLE transcription_sentences
  DROP COLUMN IF EXISTS transcription_id;

CREATE POLICY "Users can view own transcription_sentences" ON transcription_sentences
  FOR SELECT
  USING (
    stream_session_id IN (
      SELECT ss.id
      FROM stream_sessions ss
      JOIN user_sessions us ON us.id = ss.user_session_id
      WHERE us.user_id = auth.uid()
    )
  );

CREATE POLICY "Users can insert own transcription_sentences" ON transcription_sentences
  FOR INSERT
  WITH CHECK (
    stream_session_id IN (
      SELECT ss.id
      FROM stream_sessions ss
      JOIN user_sessions us ON us.id = ss.user_session_id
      WHERE us.user_id = auth.uid()
    )
  );

CREATE POLICY "Users can update own transcription_sentences" ON transcription_sentences
  FOR UPDATE
  USING (
    stream_session_id IN (
      SELECT ss.id
      FROM stream_sessions ss
      JOIN user_sessions us ON us.id = ss.user_session_id
      WHERE us.user_id = auth.uid()
    )
  )
  WITH CHECK (
    stream_session_id IN (
      SELECT ss.id
      FROM stream_sessions ss
      JOIN user_sessions us ON us.id = ss.user_session_id
      WHERE us.user_id = auth.uid()
    )
  );

-- -------------------------------
-- translations: stream keyed
-- -------------------------------
ALTER TABLE translations
  ADD COLUMN IF NOT EXISTS stream_session_id UUID REFERENCES stream_sessions(id) ON DELETE CASCADE;

UPDATE translations tr
SET stream_session_id = t.stream_session_id
FROM transcriptions t
WHERE tr.transcription_id = t.id
  AND tr.stream_session_id IS NULL;

DROP INDEX IF EXISTS idx_translations_transcription_id;
DROP INDEX IF EXISTS idx_translations_transcription_language_index;
DROP INDEX IF EXISTS idx_translations_transcription_sentences;

CREATE INDEX IF NOT EXISTS idx_translations_stream_session_id
  ON translations(stream_session_id);

CREATE INDEX IF NOT EXISTS idx_translations_stream_language_index
  ON translations(stream_session_id, target_language, sentence_index)
  WHERE sentence_index IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_translations_stream_sentence_index
  ON translations(stream_session_id, sentence_index)
  WHERE sentence_index IS NOT NULL;

ALTER TABLE translations
  DROP COLUMN IF EXISTS transcription_id;

ALTER TABLE translations
  DROP CONSTRAINT IF EXISTS translations_stream_mode_requires_stream_session;

ALTER TABLE translations
  ADD CONSTRAINT translations_stream_mode_requires_stream_session
  CHECK (mode <> 'stream' OR stream_session_id IS NOT NULL);

COMMIT;
