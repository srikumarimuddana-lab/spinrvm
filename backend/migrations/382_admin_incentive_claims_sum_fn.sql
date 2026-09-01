-- 382_admin_incentive_claims_sum_fn.sql
--
-- Purpose:
--   Replace Python-side fetch-all + Decimal sum of ride_incentive_claims
--   in admin_ride_financials (routes/admin/rides.py) with a single
--   server-side SUM. The old path fetched up to 10,000 claim rows
--   to compute one number.
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_incentive_claims_sum(timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.admin_incentive_claims_sum(
    p_start timestamptz,
    p_end   timestamptz
)
RETURNS numeric
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT COALESCE(SUM(COALESCE(bonus_amount, 0)), 0)
    FROM ride_incentive_claims
    WHERE claimed_at >= p_start
      AND claimed_at < p_end;
$$;

COMMENT ON FUNCTION public.admin_incentive_claims_sum(timestamptz, timestamptz) IS
    'Sum of bonus_amount for ride_incentive_claims in a [start, end) window. '
    'Replaces fetch-all + Python Decimal sum in admin_ride_financials.';

REVOKE EXECUTE ON FUNCTION public.admin_incentive_claims_sum(timestamptz, timestamptz)
    FROM anon, authenticated;
