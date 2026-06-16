-- 162_payout_stats_fn.sql
--
-- Purpose:
--   /payouts/stats fetched the ENTIRE payouts table (limit 10,000) only to sum
--   amounts per status and count rows in Python. This function returns those
--   status totals + counts in one aggregate round-trip.
--
--   payouts.amount is FLOAT, so it is cast ::text::numeric before summing to
--   match the old Decimal(str(float)) arithmetic to the cent.
--
--   Uses idx_payouts_status_created (status, created_at) from migration 79 for
--   the status FILTERs — no new index needed.
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_payout_stats();

CREATE OR REPLACE FUNCTION public.admin_payout_stats()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT jsonb_build_object(
        'total_paid',    COALESCE(SUM(amount::text::numeric) FILTER (WHERE status = 'completed'), 0),
        'total_pending', COALESCE(SUM(amount::text::numeric) FILTER (WHERE status = 'pending'), 0),
        'total_failed',  COALESCE(SUM(amount::text::numeric) FILTER (WHERE status = 'failed'), 0),
        'payout_count',  COUNT(*),
        'pending_count', COUNT(*) FILTER (WHERE status = 'pending')
    )
    FROM payouts;
$$;

COMMENT ON FUNCTION public.admin_payout_stats() IS
    'Payout totals by status + counts for /payouts/stats. Replaces a '
    'whole-table fetch + Python sum.';

REVOKE EXECUTE ON FUNCTION public.admin_payout_stats() FROM anon, authenticated;
