-- 388_admin_daily_ride_stats_fn.sql
--
-- Purpose:
--   Replace Python-side iteration over up to 5k rides in admin_get_driver_stats
--   (routes/admin/drivers.py) for daily ride/earnings charts with server-side
--   GROUP BY. Also lifts the silent 5000-row cap — SQL counts are exact.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_daily_ride_stats(
--       timestamptz, timestamptz, text[]);

CREATE OR REPLACE FUNCTION public.admin_daily_ride_stats(
    p_start      timestamptz,
    p_end        timestamptz,
    p_driver_ids text[] DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT COALESCE(
        jsonb_agg(jsonb_build_object(
            'day', day,
            'rides', ride_count,
            'earnings', COALESCE(earnings, 0)
        ) ORDER BY day),
        '[]'::jsonb
    )
    FROM (
        SELECT
            (created_at AT TIME ZONE 'UTC')::date AS day,
            COUNT(*)                              AS ride_count,
            COALESCE(SUM(driver_earnings) FILTER (WHERE status = 'completed'), 0) AS earnings
        FROM rides
        WHERE created_at >= p_start
          AND created_at <= p_end
          AND legacy_import_metadata = '{}'::jsonb
          AND (p_driver_ids IS NULL OR driver_id = ANY(p_driver_ids))
        GROUP BY 1
    ) t;
$$;

COMMENT ON FUNCTION public.admin_daily_ride_stats(
    timestamptz, timestamptz, text[]
) IS
    'Daily ride count + completed earnings for admin driver stats charts. '
    'Replaces 5k-ride Python-side iteration with exact server-side GROUP BY.';

REVOKE EXECUTE ON FUNCTION public.admin_daily_ride_stats(
    timestamptz, timestamptz, text[]
) FROM anon, authenticated;
