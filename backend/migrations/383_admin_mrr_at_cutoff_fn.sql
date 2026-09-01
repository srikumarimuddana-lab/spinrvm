-- 383_admin_mrr_at_cutoff_fn.sql
--
-- Purpose:
--   Replace Python-side fetch-all + temporal filter + Decimal sum of
--   driver_subscriptions in admin_get_earnings_overview (routes/admin/rides.py)
--   with a single server-side SUM. The old path fetched up to 10,000
--   subscription rows to compute two MRR numbers (current and previous).
--
--   Mirrors _active_subs_at() semantics: a subscription counts as active
--   at `p_cutoff` if it started on or before cutoff and either never
--   ended (cancelled_at/expires_at both NULL) or ended after cutoff.
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_mrr_at_cutoff(timestamptz);

CREATE OR REPLACE FUNCTION public.admin_mrr_at_cutoff(
    p_cutoff timestamptz
)
RETURNS numeric
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT COALESCE(SUM(COALESCE(price, 0)), 0)
    FROM driver_subscriptions
    WHERE COALESCE(started_at, created_at) <= p_cutoff
      AND (
          COALESCE(cancelled_at, expires_at) IS NULL
          OR COALESCE(cancelled_at, expires_at) > p_cutoff
      );
$$;

COMMENT ON FUNCTION public.admin_mrr_at_cutoff(timestamptz) IS
    'MRR snapshot: sum of price for subscriptions active at a point in time. '
    'Mirrors _active_subs_at() temporal logic. Replaces fetch-all + Python '
    'filter + Decimal sum in admin_get_earnings_overview.';

REVOKE EXECUTE ON FUNCTION public.admin_mrr_at_cutoff(timestamptz)
    FROM anon, authenticated;
