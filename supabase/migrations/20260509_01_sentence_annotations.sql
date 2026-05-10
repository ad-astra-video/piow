-- ============================================================
-- Sentence Annotations: Notes and Todos per Transcription Sentence
-- Date: 2026-05-09
--
-- Enables users to attach notes and todo items to individual
-- sentences within a transcription. Annotations persist in the
-- database and export alongside transcripts as markdown.
-- ============================================================

-- Annotations table
CREATE TABLE sentence_annotations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transcription_id UUID NOT NULL REFERENCES transcriptions(id) ON DELETE CASCADE,
  sentence_index INTEGER NOT NULL,
  sentence_text TEXT NOT NULL,
  sentence_timestamp TEXT,
  type TEXT NOT NULL CHECK (type IN ('note', 'todo')),
  content TEXT NOT NULL,
  completed BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_sentence_annotations_transcription_id ON sentence_annotations(transcription_id);
CREATE INDEX idx_sentence_annotations_type ON sentence_annotations(type);
CREATE INDEX idx_sentence_annotations_transcription_lookup ON sentence_annotations(transcription_id, sentence_index);

-- Auto-update updated_at trigger (reuses existing function if available)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'update_sentence_annotations_updated_at'
  ) THEN
    CREATE TRIGGER update_sentence_annotations_updated_at
      BEFORE UPDATE ON sentence_annotations
      FOR EACH ROW
      EXECUTE FUNCTION update_updated_at_column();
  END IF;
END
$$;

-- Enable RLS
ALTER TABLE sentence_annotations ENABLE ROW LEVEL SECURITY;

-- RLS Policies
CREATE POLICY "Service role can manage sentence_annotations" ON sentence_annotations
  FOR ALL USING (auth.jwt()->>'role' = 'service_role');

CREATE POLICY "Users can view own sentence_annotations" ON sentence_annotations
  FOR SELECT USING (
    transcription_id IN (
      SELECT id FROM transcriptions WHERE user_id = auth.uid()
    )
  );

CREATE POLICY "Users can insert own sentence_annotations" ON sentence_annotations
  FOR INSERT WITH CHECK (
    transcription_id IN (
      SELECT id FROM transcriptions WHERE user_id = auth.uid()
    )
  );

CREATE POLICY "Users can update own sentence_annotations" ON sentence_annotations
  FOR UPDATE USING (
    transcription_id IN (
      SELECT id FROM transcriptions WHERE user_id = auth.uid()
    )
  );

CREATE POLICY "Users can delete own sentence_annotations" ON sentence_annotations
  FOR DELETE USING (
    transcription_id IN (
      SELECT id FROM transcriptions WHERE user_id = auth.uid()
    )
  );
