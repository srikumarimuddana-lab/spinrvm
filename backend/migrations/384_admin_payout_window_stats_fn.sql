-- 384_admin_payout_window_stats_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of up to 200,000 payout rows in
--   admin_get_payouts_overview (routes/admin/rides.py) with server-side
--   aggregation. The old path fetched every payout row in the union of
--   current + previous windows, then partitioned/counted/summed/medianed
--   in Python. This function does it all in Postgres and returns a single
--   jsonb object.
--
--   Returned keys:
--     cur/prev: {completed_count, completed_amount, pending_count,
--                pending_amount, failed_count, failed_amount, total_count}
--     cur_median_hours / prev_median_hours: PERCENTILE_CONT(0.5) of
--       (processed_at - created_at) in hours for completed payouts
--     daily_series: [{day, paid_out, pending, failed}] for current window
--     failure_reasons: [{reason, count, amount}] top 8 in current window
--     top_drivers: [{driver_id, amount, count}] top 10 in current window
--     at_risk_drivers: [{driver_id, count, last_reason}] >= 2 failures
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_payout_window_stats(
--       timestamptz, timestamptz, timestamptz, timestamptz, text);

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
            status, amount, created_at, processed_at, driver_id, error_message,
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
            COUNT(*) FILTER (WHERE status = 'completed')                          AS completed_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'completed'), 0)          AS completed_amount,
            COUNT(*) FILTER (WHERE status IN ('pending', 'processing'))           AS pending_count,
            COALESCE(SUM(amount) FILTER (WHERE status IN ('pending','processing')), 0) AS pending_amount,
            COUNT(*) FILTER (WHERE status = 'failed')                             AS failed_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'failed'), 0)             AS failed_amount,
            COUNT(*)                                                              AS total_count
        FROM cur
    ),
    prev_stats AS (
        SELECT
            COUNT(*) FILTER (WHERE status = 'completed')                          AS completed_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'completed'), 0)          AS completed_amount,
            COUNT(*) FILTER (WHERE status IN ('pending', 'processing'))           AS pending_count,
            COALESCE(SUM(amount) FILTER (WHERE status IN ('pending','processing')), 0) AS pending_amount,
            COUNT(*) FILTER (WHERE status = 'failed')                             AS failed_count,
            COALESCE(SUM(amount) FILTER (WHERE status = 'failed'), 0)             AS failed_amount,
            COUNT(*)                                                              AS total_count
        FROM prev
    ),
    daily AS (
        SELECT
            (eff_at AT TIME ZONE 'UTC')::date AS day,
            COALESCE(SUM(amount) FILTER (WHERE status = 'completed'), 0)               AS paid_out,
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
        WHERE status = 'completed' AND driver_id IS NOT NULL
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
            WHERE status = 'completed' AND settle_hours IS NOT NULL
        ), 0),
        'prev_median_hours', COALESCE((
            SELECT round((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY settle_hours))::numeric, 2)
            FROM prev
            WHERE status = 'completed' AND settle_hours IS NOT NULL
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
) FROM anon, authenticated;
