-- 166_analytics_overview_fn.sql
--
-- Purpose:
--   /analytics/overview fetched up to 10,000 rides for the window and computed
--   status counts, completed-ride revenue/tips, a daily by-status chart, and
--   peak hours in Python. This function returns all of that from one aggregate
--   round-trip. (The endpoint caches the result 5 min, so this runs on cache
--   miss only — but it removes the last row-fetch-and-sum analytics path.)
--
--   rides(created_at) is already indexed (migration 79) — no new index.
--
-- Semantics mirror the old Python exactly:
--   * status buckets: completed / cancelled / in_progress (in_progress,
--     driver_arrived, driver_accepted) / searching / scheduled (is_scheduled).
--   * revenue/tips: SUM over completed rides only; FLOAT cast ::text::numeric
--     to match the old Decimal(str(float)).
--   * daily/hourly bucketed by created_at UTC day / hour.
--
-- Function safety: read-only, STABLE, SECURITY DEFINER, pinned search_path,
-- EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_analytics_overview(timestamptz);

CREATE OR REPLACE FUNCTION public.admin_analytics_overview(
    p_start timestamptz
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH p AS (
        SELECT status, is_scheduled, total_fare, tip_amount,
               (created_at AT TIME ZONE 'UTC')::date              AS d,
               EXTRACT(HOUR FROM (created_at AT TIME ZONE 'UTC'))::int AS hr
        FROM rides
        WHERE created_at >= p_start
    )
    SELECT jsonb_build_object(
        'total',       (SELECT COUNT(*) FROM p),
        'completed',   (SELECT COUNT(*) FROM p WHERE status = 'completed'),
        'cancelled',   (SELECT COUNT(*) FROM p WHERE status = 'cancelled'),
        'in_progress', (SELECT COUNT(*) FROM p WHERE status IN ('in_progress', 'driver_arrived', 'driver_accepted')),
        'searching',   (SELECT COUNT(*) FROM p WHERE status = 'searching'),
        'scheduled',   (SELECT COUNT(*) FROM p WHERE is_scheduled),
        'total_revenue', COALESCE((SELECT SUM(total_fare::text::numeric) FROM p WHERE status = 'completed'), 0),
        'total_tips',    COALESCE((SELECT SUM(tip_amount::text::numeric) FROM p WHERE status = 'completed'), 0),
        'daily', (
            SELECT COALESCE(jsonb_object_agg(d::text, obj), '{}'::jsonb)
            FROM (
                SELECT d, jsonb_build_object(
                    'completed', COUNT(*) FILTER (WHERE status = 'completed'),
                    'cancelled', COUNT(*) FILTER (WHERE status = 'cancelled'),
                    'total', COUNT(*)
                ) AS obj
                FROM p WHERE d IS NOT NULL GROUP BY d
            ) x
        ),
        'hourly', (
            SELECT COALESCE(jsonb_object_agg(hr::text, cnt), '{}'::jsonb)
            FROM (SELECT hr, COUNT(*) AS cnt FROM p WHERE hr IS NOT NULL GROUP BY hr) y
        )
    );
$$;

COMMENT ON FUNCTION public.admin_analytics_overview(timestamptz) IS
    'Status counts + completed revenue/tips + daily/hourly buckets for /analytics/overview.';

REVOKE EXECUTE ON FUNCTION public.admin_analytics_overview(timestamptz) FROM anon, authenticated;
