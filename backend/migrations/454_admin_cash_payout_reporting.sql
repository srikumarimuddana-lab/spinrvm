-- 454: Exclude refund-hold adjustments from fleet cash-disbursement reporting.
--
-- Clawbacks are completed payout-ledger adjustments that reduce driver
-- payable balances, but no money was transferred to a bank. Preserve their
-- balance deduction in per-driver and admin outstanding calculations; exclude
-- them from cash paid/count/settlement metrics in the fleet payout dashboards.
--
-- Rollback: re-run the CREATE OR REPLACE FUNCTION bodies from migrations 162
-- and 384, which restore the prior aggregate semantics. No data is changed.
-- migration-override-ok: intentionally replaces admin_payout_stats and
-- admin_payout_window_stats with identical signatures to exclude noncash
-- clawbacks from fleet cash reports, and admin_payout_period_snapshot with
-- its original signature/JSON shape to exclude holds at period close; execute
-- grants remain restricted below.
--
-- Dry-run: admin_payout_stats and admin_payout_window_stats should not count
-- completed clawback rows as paid or as payout volume; regular auto, standard,
-- instant and legacy payout rows retain their current accounting.

-- Period-close audit snapshots share cash-paid semantics with dashboard totals.
-- Keep the response keys and first-50 audit ID behavior from migration 391.
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
                  AND payout_type IS DISTINCT FROM 'clawback'
                  AND processed_at >= p_start
                  AND processed_at < p_end
                ORDER BY processed_at
                LIMIT 50
            ) sub
        ), '[]'::jsonb)
    ) INTO v_result
    FROM payouts
    WHERE status = 'completed'
      AND payout_type IS DISTINCT FROM 'clawback'
      AND processed_at >= p_start
      AND processed_at < p_end;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_payout_period_snapshot(timestamptz, timestamptz) IS
    'Payout period close snapshot: cash payout count + total_amount + first 50 IDs for audit. '
    'Excludes noncash clawback adjustments.';

REVOKE EXECUTE ON FUNCTION public.admin_payout_period_snapshot(timestamptz, timestamptz)
FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_payout_period_snapshot(timestamptz, timestamptz)
TO service_role;

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
    FROM payouts
    WHERE payout_type IS DISTINCT FROM 'clawback';
$$;

COMMENT ON FUNCTION public.admin_payout_stats() IS
    'Payout totals by status + counts for /payouts/stats. Replaces a '
    'whole-table fetch + Python sum.';

REVOKE EXECUTE ON FUNCTION public.admin_payout_stats() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_payout_stats() TO service_role;

CREATE OR REPLACE FUNCTION public.admin_payout_window_stats(
    p_cur_start  timestamptz,
    p_cur_end    timestamptz,
    p_prev_start timestamptz,
    p_prev_end   timestamptz,
    p_service_area_id text DEFAULT NULL
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
    WITH scoped AS (
        SELECT
            status, payout_type, amount, created_at, processed_at, driver_id, error_message,
            COALESCE(processed_at, created_at) AS eff_at,
            CASE
                WHEN processed_at IS NOT NULL AND created_at IS NOT NULL
                     AND processed_at >= created_at
                THEN EXTRACT(EPOCH FROM (processed_at - created_at)) / 3600.0
            END AS settle_hours
        FROM payouts
        WHERE COALESCE(processed_at, created_at) >= p_prev_start
          AND COALESCE(processed_at, created_at) <= p_cur_end
          AND (p_service_area_id IS NULL OR driver_id IN (
              SELECT id FROM drivers WHERE service_area_id = p_service_area_id
          ))
    ),
    cur AS (
        SELECT * FROM scoped
        WHERE eff_at >= p_cur_start AND eff_at <= p_cur_end
    ),
    prev AS (
        SELECT * FROM scoped
        WHERE eff_at >= p_prev_start AND eff_at <= p_prev_end
    ),
    cur_stats AS (
        SELECT
            COUNT(*) FILTER (WHERE status = 'completed' AND payout_type IS DISTINCT FROM 'clawback')                          AS completed_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'completed' AND payout_type IS DISTINCT FROM 'clawback'), 0)          AS completed_amount,
            COUNT(*) FILTER (WHERE status IN ('pending', 'processing'))           AS pending_count,
            COALESCE(SUM(amount) FILTER (WHERE status IN ('pending','processing')), 0) AS pending_amount,
            COUNT(*) FILTER (WHERE status = 'failed')                             AS failed_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'failed'), 0)             AS failed_amount,
            COUNT(*) FILTER (WHERE payout_type IS DISTINCT FROM 'clawback') AS total_count
        FROM cur
    ),
    prev_stats AS (
        SELECT
            COUNT(*) FILTER (WHERE status = 'completed' AND payout_type IS DISTINCT FROM 'clawback')                          AS completed_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'completed' AND payout_type IS DISTINCT FROM 'clawback'), 0)          AS completed_amount,
            COUNT(*) FILTER (WHERE status IN ('pending', 'processing'))           AS pending_count,
            COALESCE(SUM(amount) FILTER (WHERE status IN ('pending','processing')), 0) AS pending_amount,
            COUNT(*) FILTER (WHERE status = 'failed')                             AS failed_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'failed'), 0)             AS failed_amount,
            COUNT(*) FILTER (WHERE payout_type IS DISTINCT FROM 'clawback') AS total_count
        FROM prev
    ),
    daily AS (
        SELECT
            (eff_at AT TIME ZONE 'UTC')::date AS day,
            COALESCE(SUM(amount) FILTER (WHERE status = 'completed' AND payout_type IS DISTINCT FROM 'clawback'), 0)               AS paid_out,
            COALESCE(SUM(amount) FILTER (WHERE status IN ('pending','processing')), 0) AS pending_amt,
            COALESCE(SUM(amount) FILTER (WHERE status = 'failed'), 0)                  AS failed_amt
        FROM cur
        GROUP BY 1
        ORDER BY 1
    ),
    fail_reasons AS (
        SELECT
            CASE
                WHEN length(COALESCE(trim(error_message), '')) > 60
                THEN left(trim(error_message), 60) || '…'
                ELSE COALESCE(NULLIF(trim(error_message), ''), 'Unknown')
            END                    AS reason,
            COUNT(*)               AS cnt,
            COALESCE(SUM(amount), 0) AS amt
        FROM cur
        WHERE status = 'failed'
        GROUP BY 1
        ORDER BY COUNT(*) DESC, SUM(amount) DESC
        LIMIT 8
    ),
    top_drv AS (
        SELECT driver_id,
               COALESCE(SUM(amount), 0) AS amt,
               COUNT(*)                 AS cnt
        FROM cur
        WHERE status = 'completed' AND payout_type IS DISTINCT FROM 'clawback' AND driver_id IS NOT NULL
        GROUP BY 1
        ORDER BY SUM(amount) DESC
        LIMIT 10
    ),
    at_risk_raw AS (
        SELECT driver_id, COUNT(*) AS cnt
        FROM cur
        WHERE status = 'failed' AND driver_id IS NOT NULL
        GROUP BY 1
        HAVING COUNT(*) >= 2
        ORDER BY COUNT(*) DESC
        LIMIT 10
    ),
    at_risk AS (
        SELECT DISTINCT ON (ar.driver_id)
            ar.driver_id,
            ar.cnt,
            COALESCE(c.error_message, 'Unknown') AS last_reason
        FROM at_risk_raw ar
        JOIN cur c ON c.driver_id = ar.driver_id AND c.status = 'failed'
        ORDER BY ar.driver_id, c.created_at DESC
    )
    SELECT jsonb_build_object(
        'cur',  (SELECT row_to_json(cur_stats)::jsonb FROM cur_stats),
        'prev', (SELECT row_to_json(prev_stats)::jsonb FROM prev_stats),
        'cur_median_hours', COALESCE((
            SELECT round((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY settle_hours))::numeric, 2)
            FROM cur
            WHERE status = 'completed' AND payout_type IS DISTINCT FROM 'clawback' AND settle_hours IS NOT NULL
        ), 0),
        'prev_median_hours', COALESCE((
            SELECT round((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY settle_hours))::numeric, 2)
            FROM prev
            WHERE status = 'completed' AND payout_type IS DISTINCT FROM 'clawback' AND settle_hours IS NOT NULL
        ), 0),
        'daily_series', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'day', day, 'paid_out', paid_out, 'pending', pending_amt, 'failed', failed_amt
            )) FROM daily
        ), '[]'::jsonb),
        'failure_reasons', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'reason', reason, 'count', cnt, 'amount', amt
            )) FROM fail_reasons
        ), '[]'::jsonb),
        'top_drivers', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'driver_id', driver_id, 'amount', amt, 'count', cnt
            )) FROM top_drv
        ), '[]'::jsonb),
        'at_risk_drivers', COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'driver_id', driver_id, 'count', cnt, 'last_reason', last_reason
            ) ORDER BY cnt DESC) FROM at_risk
        ), '[]'::jsonb)
    ) INTO v_result;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_payout_window_stats(
    timestamptz, timestamptz, timestamptz, timestamptz, text
) IS
    'Windowed payout aggregation for admin_get_payouts_overview: counts/sums '
    'by status for current+previous windows, median settlement hours, daily '
    'series, failure reasons, top drivers, at-risk drivers. Replaces 200k-row '
    'Python-side fetch+loop.';

REVOKE EXECUTE ON FUNCTION public.admin_payout_window_stats(
    timestamptz, timestamptz, timestamptz, timestamptz, text
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.admin_payout_window_stats(
    timestamptz, timestamptz, timestamptz, timestamptz, text
) TO service_role;
