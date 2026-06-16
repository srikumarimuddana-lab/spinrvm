-- 160_ride_daily_counts_fn.sql
--
-- Purpose:
--   Replace two "pull rows, count in Python" analytics paths with a single
--   GROUP BY in Postgres:
--     * /rides/trend  fetched up to 50,000 created_at values and bucketed them
--       per day in a Python loop.
--     * /rides/stats  daily_chart issued 14 sequential COUNT round-trips
--       (get_ride_count_by_date_range once per day).
--   Both are now one call to admin_ride_daily_counts, which returns the per-day
--   ride count (by created_at, UTC calendar day) for a window. Days with no
--   rides are absent from the result; the caller fills gaps with zero.
--
--   Mirrors get_ride_count_by_date_range exactly: counts ALL rides by
--   created_at in [p_start, p_end).
--
-- rides(created_at) is already indexed (migration 79), so no new index here.
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_ride_daily_counts(timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.admin_ride_daily_counts(
    p_start timestamptz,
    p_end   timestamptz
)
RETURNS TABLE (day date, rides bigint)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT (created_at AT TIME ZONE 'UTC')::date AS day, COUNT(*) AS rides
    FROM rides
    WHERE created_at >= p_start AND created_at < p_end
    GROUP BY 1
    ORDER BY 1;
$$;

COMMENT ON FUNCTION public.admin_ride_daily_counts(timestamptz, timestamptz) IS
    'Per-day ride counts (by created_at, UTC) for a window. Replaces row-fetch '
    'bucketing in /rides/trend and the 14-query daily_chart in /rides/stats.';

REVOKE EXECUTE ON FUNCTION public.admin_ride_daily_counts(timestamptz, timestamptz)
    FROM anon, authenticated;
