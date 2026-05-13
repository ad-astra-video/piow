-- ============================================================
-- Backfill missing/incorrect RLS policies
-- Date: 2026-05-13
--
-- Goals:
-- 1) Fix sentence_annotations policy drift (remove permissive INSERT policy).
-- 2) Ensure api_usage has RLS + baseline policies.
--
-- This migration is idempotent and safe to re-run.
-- ============================================================

BEGIN;

-- ------------------------------------------------------------------
-- sentence_annotations: replace permissive policy with owner-scoped set
-- ------------------------------------------------------------------
DO $$
BEGIN
  IF to_regclass('public.sentence_annotations') IS NOT NULL THEN
    ALTER TABLE public.sentence_annotations ENABLE ROW LEVEL SECURITY;

    DROP POLICY IF EXISTS "Enable insert for authenticated users only" ON public.sentence_annotations;

    DROP POLICY IF EXISTS "Service role can manage sentence_annotations" ON public.sentence_annotations;
    DROP POLICY IF EXISTS "Users can view own sentence_annotations" ON public.sentence_annotations;
    DROP POLICY IF EXISTS "Users can insert own sentence_annotations" ON public.sentence_annotations;
    DROP POLICY IF EXISTS "Users can update own sentence_annotations" ON public.sentence_annotations;
    DROP POLICY IF EXISTS "Users can delete own sentence_annotations" ON public.sentence_annotations;

    CREATE POLICY "Service role can manage sentence_annotations" ON public.sentence_annotations
      FOR ALL
      USING ((auth.jwt() ->> 'role') = 'service_role');

    CREATE POLICY "Users can view own sentence_annotations" ON public.sentence_annotations
      FOR SELECT
      USING (
        transcription_id IN (
          SELECT id FROM public.transcriptions WHERE user_id = auth.uid()
        )
      );

    CREATE POLICY "Users can insert own sentence_annotations" ON public.sentence_annotations
      FOR INSERT
      WITH CHECK (
        transcription_id IN (
          SELECT id FROM public.transcriptions WHERE user_id = auth.uid()
        )
      );

    CREATE POLICY "Users can update own sentence_annotations" ON public.sentence_annotations
      FOR UPDATE
      USING (
        transcription_id IN (
          SELECT id FROM public.transcriptions WHERE user_id = auth.uid()
        )
      )
      WITH CHECK (
        transcription_id IN (
          SELECT id FROM public.transcriptions WHERE user_id = auth.uid()
        )
      );

    CREATE POLICY "Users can delete own sentence_annotations" ON public.sentence_annotations
      FOR DELETE
      USING (
        transcription_id IN (
          SELECT id FROM public.transcriptions WHERE user_id = auth.uid()
        )
      );
  END IF;
END
$$;

-- ------------------------------------------------------------------
-- api_usage: add RLS and baseline policies if table exists
-- ------------------------------------------------------------------
DO $$
BEGIN
  IF to_regclass('public.api_usage') IS NOT NULL THEN
    ALTER TABLE public.api_usage ENABLE ROW LEVEL SECURITY;

    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename = 'api_usage'
        AND policyname = 'Service role can manage api_usage'
    ) THEN
      CREATE POLICY "Service role can manage api_usage" ON public.api_usage
        FOR ALL
        USING ((auth.jwt() ->> 'role') = 'service_role');
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM pg_policies
      WHERE schemaname = 'public'
        AND tablename = 'api_usage'
        AND policyname = 'Users can view own api_usage'
    ) THEN
      CREATE POLICY "Users can view own api_usage" ON public.api_usage
        FOR SELECT
        USING (actor_type = 'user' AND user_id = auth.uid());
    END IF;
  END IF;
END
$$;

COMMIT;
