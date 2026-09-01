-- 390_admin_driver_bonus_summary_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of up to 10k driver_bonuses rows in
--   admin_get_driver_payouts_summary (routes/admin/drivers.py) with
--   server-side SUM aggregation.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_driver_bonus_summary(text, timestamptz);

CREATE OR REPLACE FUNCTION public.admin_driver_bonus_summary(
    p_driver_id  text,
    p_year_start timestamptz DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT jsonb_build_object(
        'total_bonuses', COALESCE(SUM(amount), 0),
        'ytd_bonuses',   COALESCE(SUM(amount) FILTER (
                             WHERE created_at >= p_year_start), 0)
    )
    FROM driver_bonuses
    WHERE driver_id = p_driver_id;
$$;

COMMENT ON FUNCTION public.admin_driver_bonus_summary(text, timestamptz) IS
    'Per-driver bonus aggregation: lifetime + YTD totals. '
    'Replaces 10k driver_bonuses fetch + Python sum.';

REVOKE EXECUTE ON FUNCTION public.admin_driver_bonus_summary(text, timestamptz)
FROM anon, authenticated;
