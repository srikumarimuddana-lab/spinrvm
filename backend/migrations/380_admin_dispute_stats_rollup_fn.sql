-- 380_admin_dispute_stats_rollup_fn.sql
--
-- Purpose:
--   Replace Python-side fetch-all + loop in admin_get_dispute_stats
--   (routes/admin/support.py) with a single server-side GROUP BY.
--   The old path fetched up to 10,000 dispute rows, looped counting
--   by status and summing refund_amount — O(N) rows transferred for
--   4 integers and 1 decimal.
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_dispute_stats_rollup();

CREATE OR REPLACE FUNCTION public.admin_dispute_stats_rollup()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT jsonb_build_object(
        'open',           COALESCE(SUM(CASE WHEN status = 'open'         THEN 1 ELSE 0 END), 0),
        'under_review',   COALESCE(SUM(CASE WHEN status = 'under_review' THEN 1 ELSE 0 END), 0),
        'resolved',       COALESCE(SUM(CASE WHEN status = 'resolved'     THEN 1 ELSE 0 END), 0),
        'rejected',       COALESCE(SUM(CASE WHEN status = 'rejected'     THEN 1 ELSE 0 END), 0),
        'total_refunded',
            COALESCE(SUM(
                CASE WHEN status = 'resolved'
                     THEN COALESCE(refund_amount, 0)
                     ELSE 0
                END
            ), 0)
    )
    FROM disputes;
$$;

COMMENT ON FUNCTION public.admin_dispute_stats_rollup() IS
    'Dispute counts by status + resolved refund total. '
    'Replaces fetch-all + Python loop in admin_get_dispute_stats.';

REVOKE EXECUTE ON FUNCTION public.admin_dispute_stats_rollup()
    FROM anon, authenticated;
