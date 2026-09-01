-- 385_admin_subscription_stats_rollup_fn.sql
--
-- Purpose:
--   Replace Python-side fetch of ALL driver_subscriptions (10k) +
--   ALL subscription_payments (20k) in admin_get_subscription_stats
--   (routes/admin/subscriptions.py) with server-side aggregation.
--
--   Returned keys:
--     total_real / active / expired / cancelled: subscriber counts
--       (excludes pending/superseded checkout rows without payment_status='paid')
--     active_mrr: sum of active subscription prices
--     total_revenue / range_revenue / range_count: payment sums
--     plan_breakdown: [{plan_id, name, count, active, revenue}]
--     daily_revenue: [{day, amount}] within date range
--     daily_subscribers: [{day, count}] new subs within date range
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_subscription_stats_rollup(
--       timestamptz, timestamptz, text[]);

CREATE OR REPLACE FUNCTION public.admin_subscription_stats_rollup(
    p_range_start timestamptz,
    p_range_end   timestamptz,
    p_area_ids    text[] DEFAULT NULL
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
    WITH scoped_subs AS (
        SELECT ds.*
        FROM driver_subscriptions ds
        WHERE p_area_ids IS NULL
           OR ds.driver_id::text IN (
               SELECT id FROM drivers WHERE service_area_id = ANY(p_area_ids)
           )
    ),
    real_subs AS (
        SELECT * FROM scoped_subs
        WHERE payment_status = 'paid'
           OR status NOT IN ('pending', 'superseded')
    ),
    status_counts AS (
        SELECT
            COUNT(*)                                      AS total_real,
            COUNT(*) FILTER (WHERE status = 'active')     AS active_cnt,
            COUNT(*) FILTER (WHERE status = 'expired')    AS expired_cnt,
            COUNT(*) FILTER (WHERE status = 'cancelled')  AS cancelled_cnt
        FROM real_subs
    ),
    active_mrr AS (
        SELECT COALESCE(SUM(price), 0) AS mrr
        FROM scoped_subs
        WHERE status = 'active'
    ),
    scoped_payments AS (
        SELECT sp.*
        FROM subscription_payments sp
        WHERE p_area_ids IS NULL
           OR sp.driver_id::text IN (
               SELECT id FROM drivers WHERE service_area_id = ANY(p_area_ids)
           )
    ),
    total_rev AS (
        SELECT COALESCE(SUM(amount), 0) AS total_revenue FROM scoped_payments
    ),
    range_rev AS (
        SELECT COALESCE(SUM(amount), 0) AS range_revenue,
               COUNT(*)                 AS range_count
        FROM scoped_payments
        WHERE created_at >= p_range_start AND created_at <= p_range_end
    ),
    plan_sub_stats AS (
        SELECT
            COALESCE(plan_id::text, 'unknown') AS pid,
            MAX(plan_name)               AS sub_plan_name,
            COUNT(*)                                      AS sub_count,
            COUNT(*) FILTER (WHERE status = 'active')     AS active_count
        FROM real_subs
        GROUP BY COALESCE(plan_id::text, 'unknown')
    ),
    plan_pay_stats AS (
        SELECT
            COALESCE(plan_id::text, 'unknown') AS pid,
            MAX(plan_name)               AS pay_plan_name,
            COALESCE(SUM(amount), 0)     AS revenue
        FROM scoped_payments
        GROUP BY COALESCE(plan_id::text, 'unknown')
    ),
    plan_merged AS (
        SELECT
            COALESCE(s.pid, p.pid) AS plan_id,
            COALESCE(
                s.sub_plan_name,
                p.pay_plan_name,
                (SELECT name FROM subscription_plans sp2
                 WHERE sp2.id::text = COALESCE(s.pid, p.pid) LIMIT 1),
                'Unknown'
            ) AS name,
            COALESCE(s.sub_count, 0)    AS count,
            COALESCE(s.active_count, 0) AS active,
            COALESCE(p.revenue, 0)      AS revenue
        FROM plan_sub_stats s
        FULL OUTER JOIN plan_pay_stats p ON s.pid = p.pid
    ),
    daily_rev AS (
        SELECT
            (created_at AT TIME ZONE 'UTC')::date AS day,
            COALESCE(SUM(amount), 0)              AS amount
        FROM scoped_payments
        WHERE created_at >= p_range_start AND created_at <= p_range_end
        GROUP BY 1
        ORDER BY 1
    ),
    new_subs_in_range AS (
        SELECT * FROM real_subs
        WHERE COALESCE(created_at, started_at) >= p_range_start
          AND COALESCE(created_at, started_at) <= p_range_end
    ),
    daily_subs AS (
        SELECT
            (COALESCE(created_at, started_at) AT TIME ZONE 'UTC')::date AS day,
            COUNT(*) AS cnt
        FROM new_subs_in_range
        GROUP BY 1
        ORDER BY 1
    )
    SELECT jsonb_build_object(
        'total_real',       (SELECT total_real FROM status_counts),
        'active',           (SELECT active_cnt FROM status_counts),
        'expired',          (SELECT expired_cnt FROM status_counts),
        'cancelled',        (SELECT cancelled_cnt FROM status_counts),
        'active_mrr',       (SELECT mrr FROM active_mrr),
        'total_revenue',    (SELECT total_revenue FROM total_rev),
        'range_revenue',    (SELECT range_revenue FROM range_rev),
        'range_count',      (SELECT range_count FROM range_rev),
        'plan_breakdown',   COALESCE((
            SELECT jsonb_agg(jsonb_build_object(
                'plan_id', plan_id, 'name', name, 'count', count,
                'active', active, 'revenue', revenue
            )) FROM plan_merged
        ), '[]'::jsonb),
        'daily_revenue',    COALESCE((
            SELECT jsonb_agg(jsonb_build_object('day', day, 'amount', amount))
            FROM daily_rev
        ), '[]'::jsonb),
        'daily_subscribers', COALESCE((
            SELECT jsonb_agg(jsonb_build_object('day', day, 'count', cnt))
            FROM daily_subs
        ), '[]'::jsonb)
    ) INTO v_result;

    RETURN v_result;
END;
$$;

COMMENT ON FUNCTION public.admin_subscription_stats_rollup(
    timestamptz, timestamptz, text[]
) IS
    'Subscription stats aggregation for admin_get_subscription_stats: subscriber '
    'counts by status, MRR, total/range revenue, per-plan breakdown, daily charts. '
    'Replaces 10k-sub + 20k-payment Python-side fetch+loop.';

REVOKE EXECUTE ON FUNCTION public.admin_subscription_stats_rollup(
    timestamptz, timestamptz, text[]
) FROM anon, authenticated;
