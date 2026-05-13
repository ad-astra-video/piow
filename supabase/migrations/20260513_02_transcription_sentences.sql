-- ============================================================
-- Transcription Sentences: per-sentence storage for live streams
-- Date: 2026-05-13
--
-- Replaces the concatenated `transcriptions.text` blob with one
-- row per sentence, carrying the optional translated text inline.
-- The `transcriptions` row remains as the session header that
-- sentence_annotations and translations still reference.
-- ============================================================

CREATE TABLE transcription_sentences (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transcription_id UUID NOT NULL REFERENCES transcriptions(id) ON DELETE CASCADE,
  sentence_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  translated_text TEXT,
  timestamp TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (transcription_id, sentence_index)
);

CREATE INDEX idx_transcription_sentences_transcription_id
  ON transcription_sentences(transcription_id);

CREATE INDEX idx_transcription_sentences_lookup
  ON transcription_sentences(transcription_id, sentence_index);

-- Enable RLS
ALTER TABLE transcription_sentences ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role can manage transcription_sentences"
  ON transcription_sentences FOR ALL
  USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own transcription_sentences"
  ON transcription_sentences FOR SELECT
  USING (
    transcription_id IN (
      SELECT id FROM transcriptions WHERE user_id = auth.uid()
    )
  );

CREATE POLICY "Users can insert own transcription_sentences"
  ON transcription_sentences FOR INSERT
  WITH CHECK (
    transcription_id IN (
      SELECT id FROM transcriptions WHERE user_id = auth.uid()
    )
  );

CREATE POLICY "Users can update own transcription_sentences"
  ON transcription_sentences FOR UPDATE
  USING (
    transcription_id IN (
      SELECT id FROM transcriptions WHERE user_id = auth.uid()
    )
  );
