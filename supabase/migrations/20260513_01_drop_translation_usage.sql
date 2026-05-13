-- Remove legacy translation usage tracking now that translation is free and
-- usage/quota enforcement has been removed from the app.

BEGIN;

DROP TABLE IF EXISTS translation_usage CASCADE;

COMMIT;
