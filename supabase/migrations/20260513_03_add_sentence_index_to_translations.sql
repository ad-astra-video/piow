-- ============================================================
-- Add sentence_index to translations for proper linking to sentences
-- Date: 2026-05-13
--
-- This migration adds a sentence_index column to link translations
-- directly to their corresponding sentences, replacing the fragile
-- text-based matching that was causing first translations to be lost.
-- ============================================================

ALTER TABLE translations
ADD COLUMN IF NOT EXISTS sentence_index INTEGER;

-- Create index for efficient lookups by transcription + language + index
CREATE INDEX IF NOT EXISTS idx_translations_transcription_language_index
ON translations(transcription_id, target_language, sentence_index)
WHERE sentence_index IS NOT NULL;

-- Create index for finding all translations with sentence_index for a transcription
CREATE INDEX IF NOT EXISTS idx_translations_transcription_sentences
ON translations(transcription_id, sentence_index)
WHERE sentence_index IS NOT NULL;
