-- 386_admin_driver_referral_board_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of 20k referral_payouts rows in
--   admin_get_driver_referral_leaderboard (routes/admin/drivers.py) with
--   server-side GROUP BY. Returns top N referrers + fleet-wide totals.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_driver_referral_board(int);

CREATE OR REPLACE FUNCTION public.admin_driver_referral_board(
    p_limit int DEFAULT 20
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
    WITH agg AS (
        SELECT
            referrer_user_id,
            COUNT(*)                                                 AS total,
            COUNT(*) FILTER (WHERE status = 'paid')                  AS qualified,
            COALESCE(SUM(referrer_reward) FILTER (WHERE status = 'paid'), 0) AS earnings
        FROM referral_payouts
        WHERE kind = 'driver' AND referrer_user_id IS NOT NULL
        GROUP BY referrer_user_id
    ),
    fleet AS (
        SELECT
            COALESCE(SUM(total), 0)    AS fleet_total_referrals,
            COUNT(*)                   AS fleet_total_referrers
        FROM agg
    ),
    top_referrers AS (
        SELECT * FROM agg
        ORDER BY total DESC
        LIMIT p_limit
    )
    SELECT jsonb_build_object(
        'fleet_total_referrals', (SELECT fleet_total_referrals FROM fleet),
        'fleet_total_referrers', (SELECT fleet_total_referrers FROM fleet),
        'leaders', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'referrer_user_id', referrer_user_id,
                'total', total,
                'qualified', qualified,
                'earnings', earnings
            ) ORDER BY total DESC)
            FROM top_referrers
        ), '[]'::jsonb)
    ) INTO v_result;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_driver_referral_board(int) IS
    'Driver referral leaderboard aggregation: top N referrers by total '
    'claims + fleet-wide totals. Replaces 20k referral_payouts fetch.';

REVOKE EXECUTE ON FUNCTION public.admin_driver_referral_board(int)
FROM anon, authenticated;
