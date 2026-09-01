-- 391_admin_payout_period_snapshot_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of 5k completed payouts in
--   admin_close_payout_period (routes/admin/rides.py) just to count
--   rows and sum amounts. The first 50 payout IDs are still returned
--   for the audit trail.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_payout_period_snapshot(timestamptz, timestamptz);

CREATE OR REPLACE FUNCTION public.admin_payout_period_snapshot(
    p_start timestamptz,
    p_end   timestamptz
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
        'payout_count',  COUNT(*),
        'total_amount',  COALESCE(SUM(amount), 0),
        'payout_ids',    COALESCE((
            SELECT jsonb_agg(id)
            FROM (
                SELECT id FROM payouts
                WHERE status = 'completed'
                  AND processed_at >= p_start
                  AND processed_at < p_end
                ORDER BY processed_at
                LIMIT 50
            ) sub
        ), '[]'::jsonb)
    ) INTO v_result
    FROM payouts
    WHERE status = 'completed'
      AND processed_at >= p_start
      AND processed_at < p_end;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_payout_period_snapshot(timestamptz, timestamptz) IS
    'Payout period close snapshot: count + total_amount + first 50 IDs for audit. '
    'Replaces 5k completed-payouts fetch + Python sum.';

REVOKE EXECUTE ON FUNCTION public.admin_payout_period_snapshot(timestamptz, timestamptz)
FROM anon, authenticated;
