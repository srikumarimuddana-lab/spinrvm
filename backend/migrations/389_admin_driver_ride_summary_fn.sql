-- 389_admin_driver_ride_summary_fn.sql
--
-- Purpose:
--   Replace Python-side iteration over up to 5k/10k rides in
--   admin_get_driver_live_stats and admin_get_driver_payouts_summary
--   (routes/admin/drivers.py) with server-side aggregation.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_driver_ride_summary(text, timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.admin_driver_ride_summary(
    p_driver_id    text,
    p_year_start   timestamptz DEFAULT NULL,
    p_recent_cutoff timestamptz DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    v_result jsonb;
BEGIN
    SELECT jsonb_build_object(
        'total_assigned',      COUNT(*),
        'completed_count',     COUNT(*) FILTER (WHERE status = 'completed'),
        'lifetime_earnings',   COALESCE(SUM(driver_earnings) FILTER (
                                   WHERE status = 'completed'
                                     AND legacy_import_metadata = '{}'::jsonb), 0),
        'lifetime_tips',       COALESCE(SUM(tip_amount) FILTER (
                                   WHERE status = 'completed'
                                     AND legacy_import_metadata = '{}'::jsonb), 0),
        'avg_rider_rating',    ROUND(AVG(rider_rating) FILTER (
                                   WHERE status = 'completed'
                                     AND rider_rating > 0)::numeric, 2),
        'cancelled_by_driver', COUNT(*) FILTER (
                                   WHERE status = 'cancelled'
                                     AND cancellation_reason ILIKE '%driver%'),
        'ytd_earnings',        COALESCE(SUM(driver_earnings) FILTER (
                                   WHERE status = 'completed'
                                     AND legacy_import_metadata = '{}'::jsonb
                                     AND COALESCE(ride_completed_at, created_at) >= p_year_start), 0),
        'ytd_tips',            COALESCE(SUM(tip_amount) FILTER (
                                   WHERE status = 'completed'
                                     AND legacy_import_metadata = '{}'::jsonb
                                     AND COALESCE(ride_completed_at, created_at) >= p_year_start), 0),
        'active_days_recent',  COUNT(DISTINCT COALESCE(ride_completed_at, created_at)::date) FILTER (
                                   WHERE status = 'completed'
                                     AND COALESCE(ride_completed_at, created_at) >= p_recent_cutoff)
    ) INTO v_result
    FROM rides
    WHERE driver_id = p_driver_id;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_driver_ride_summary(text, timestamptz, timestamptz) IS
    'Per-driver ride aggregation: counts, earnings, tips, rating, cancellation, '
    'YTD, and active days. Replaces 5k-10k ride fetch + Python iteration.';

REVOKE EXECUTE ON FUNCTION public.admin_driver_ride_summary(text, timestamptz, timestamptz)
FROM anon, authenticated;
