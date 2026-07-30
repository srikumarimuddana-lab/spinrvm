-- 273_driver_onboarding_reminder_counts_fn.sql
-- Rollback:
--   DROP FUNCTION IF EXISTS public.driver_onboarding_reminder_counts(text[]);
--   (The caller falls back to a client-side count when the RPC is missing, so
--    dropping this degrades the reminder cap rather than breaking the loop.)
--
-- Per-driver/per-type reminder counts for the daily onboarding reminder cap.
--
-- The loop previously counted rows client-side by reading the claim log with a
-- row limit. That silently under-counted: the limit was sized against the cap
-- (200 drivers x 2 types x cap+1) but the log also holds rows written before
-- the cap existed, when the loop ran unbounded — a driver pending for 60 days
-- has ~120 rows. Over the limit, PostgREST truncates with no ORDER BY, so
-- arbitrary rows vanish, counts come back low, and capped drivers get pushed
-- again. Aggregating in the database removes the limit from the path entirely.
--
-- STABLE + no writes. SECURITY DEFINER only so the backend service role's
-- access does not depend on RLS on the log table (which denies all client
-- access by design, migration 139); search_path is pinned per convention.
--
-- No new index: the existing UNIQUE index on
-- (driver_id, reminder_type, local_date) from migration 139 already covers
-- this GROUP BY and supports an index-only scan.

CREATE OR REPLACE FUNCTION public.driver_onboarding_reminder_counts(p_driver_ids text[])
RETURNS TABLE (driver_id text, reminder_type text, sent_count bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT l.driver_id, l.reminder_type, COUNT(*)::bigint
    FROM public.driver_onboarding_reminder_log l
    WHERE l.driver_id = ANY(p_driver_ids)
    GROUP BY l.driver_id, l.reminder_type;
$$;

REVOKE ALL ON FUNCTION public.driver_onboarding_reminder_counts(text[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.driver_onboarding_reminder_counts(text[]) FROM anon, authenticated;
GRANT EXECUTE ON FUNCTION public.driver_onboarding_reminder_counts(text[]) TO service_role;

COMMENT ON FUNCTION public.driver_onboarding_reminder_counts(text[]) IS
    'Per-driver/per-type onboarding reminder counts, backing the daily repeat cap. '
    'Backend service role only — the underlying log table denies all client access.';

NOTIFY pgrst, 'reload schema';
