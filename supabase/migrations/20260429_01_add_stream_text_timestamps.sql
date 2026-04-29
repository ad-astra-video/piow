-- Add JSONB storage for out-of-band text timestamp windows on active stream sessions.
ALTER TABLE stream_sessions
ADD COLUMN IF NOT EXISTS text_timestamps JSONB NOT NULL DEFAULT '[]'::jsonb;
