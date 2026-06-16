-- 163_earnings_overview_aggregates_fn.sql
--
-- Purpose:
--   /earnings/overview pulled four windows of rides (current/previous
--   completed + cancelled, limit=50000 each) plus refunds (limit=10000 x2) and
--   aggregated them in Python (GBV, platform margin, distinct active
--   riders/drivers, surge revenue, promo, GST/PST from the tax_breakdown JSONB,
--   cancellation counts/revenue, refund totals, and a daily series). These
--   functions do that work in Postgres.
--
--   Three functions:
--     admin_earnings_overview_agg(start,end,area)  -> jsonb of completed +
--         cancelled metrics for one window (called for current & previous).
--     admin_earnings_daily_series(start,end,area)  -> per-day gbv/trips/
--         net_revenue over completed rides (current window line chart).
--     admin_earnings_refunds(start,end,area)       -> refund $ + count from
--         disputes resolved in the window (area-scoped via the ride set, same
--         as the old Python cross-reference).
--
--   NOT converted (left in Python for now): the Spinr Pass MRR snapshot, which
--   reads driver_subscriptions (bounded) through point-in-time _active_subs_at
--   logic — a separate follow-up.
--
-- Semantics mirror the previous Python exactly:
--   * completed window = ride_completed_at in [start, end]; cancelled window =
--     created_at in [start, end] (cancelled rows have null ride_completed_at).
--   * money columns are FLOAT -> cast ::text::numeric so sums match the old
--     Decimal(str(float)) arithmetic to the cent.
--   * surge_revenue = sum(total_fare - total_fare/surge_multiplier) over rides
--     with surge_multiplier > 1.
--   * GST bucket = tax_breakdown keys containing 'gst' or equal to 'hst';
--     PST bucket = keys containing 'pst' or 'qst'; amount = value->>'amount'.
--   * cancel attribution: 'driver' in reason -> driver; else 'rider'/'user' ->
--     rider (driver takes precedence, matching the old elif).
--   * refunds: disputes resolved in [start,end]; when an area is set, only
--     disputes whose ride_id is in that window's completed+cancelled ride set.
--
-- Money-function safety: read-only, STABLE, SECURITY DEFINER, pinned
-- search_path, EXECUTE revoked from anon/authenticated.
--
-- Note: the two new indexes use CONCURRENTLY (hot tables); the runner detects
-- CONCURRENTLY and applies the file in autocommit mode.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_earnings_overview_agg(timestamptz,timestamptz,text);
--   DROP FUNCTION IF EXISTS public.admin_earnings_daily_series(timestamptz,timestamptz,text);
--   DROP FUNCTION IF EXISTS public.admin_earnings_refunds(timestamptz,timestamptz,text);
--   DROP INDEX CONCURRENTLY IF EXISTS idx_rides_cancelled_created;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_disputes_resolved_at;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_cancelled_created
    ON public.rides (created_at) WHERE status = 'cancelled';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_disputes_resolved_at
    ON public.disputes (resolved_at);

CREATE OR REPLACE FUNCTION public.admin_earnings_overview_agg(
    p_start           timestamptz,
    p_end             timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
WITH completed AS (
    SELECT total_fare, admin_earnings, rider_id, driver_id,
           surge_multiplier, discount_amount, tax_breakdown
    FROM rides
    WHERE status = 'completed'
      AND ride_completed_at >= p_start AND ride_completed_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
),
cancelled AS (
    SELECT cancellation_fee_admin, cancellation_reason
    FROM rides
    WHERE status = 'cancelled'
      AND created_at >= p_start AND created_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
),
tax AS (
    SELECT t.key AS k, (t.value ->> 'amount') AS amt
    FROM completed c, LATERAL jsonb_each(c.tax_breakdown) t
    WHERE jsonb_typeof(c.tax_breakdown) = 'object'
)
SELECT jsonb_build_object(
    'gbv',           COALESCE((SELECT SUM(total_fare::text::numeric) FROM completed), 0),
    'platform',      COALESCE((SELECT SUM(admin_earnings::text::numeric) FROM completed), 0),
    'trips',         (SELECT COUNT(*) FROM completed),
    'riders',        (SELECT COUNT(DISTINCT rider_id) FROM completed WHERE rider_id IS NOT NULL),
    'drivers',       (SELECT COUNT(DISTINCT driver_id) FROM completed WHERE driver_id IS NOT NULL),
    'surge_revenue', COALESCE((
        SELECT SUM(total_fare::text::numeric - total_fare::text::numeric / surge_multiplier::text::numeric)
        FROM completed
        WHERE surge_multiplier IS NOT NULL AND surge_multiplier::text::numeric > 1
    ), 0),
    'promo_spend',   COALESCE((SELECT SUM(discount_amount::text::numeric) FROM completed), 0),
    'promo_count',   (SELECT COUNT(*) FROM completed WHERE COALESCE(discount_amount, 0)::text::numeric > 0),
    'gst_collected', COALESCE((SELECT SUM(amt::numeric) FROM tax WHERE lower(k) LIKE '%gst%' OR lower(k) = 'hst'), 0),
    'pst_collected', COALESCE((SELECT SUM(amt::numeric) FROM tax WHERE lower(k) LIKE '%pst%' OR lower(k) LIKE '%qst%'), 0),
    'cx_count',      (SELECT COUNT(*) FROM cancelled),
    'cx_revenue',    COALESCE((SELECT SUM(cancellation_fee_admin::text::numeric) FROM cancelled), 0),
    'cx_rider_cancels', (
        SELECT COUNT(*) FROM cancelled
        WHERE lower(COALESCE(cancellation_reason, '')) NOT LIKE '%driver%'
          AND (lower(COALESCE(cancellation_reason, '')) LIKE '%rider%'
               OR lower(COALESCE(cancellation_reason, '')) LIKE '%user%')
    ),
    'cx_driver_cancels', (
        SELECT COUNT(*) FROM cancelled WHERE lower(COALESCE(cancellation_reason, '')) LIKE '%driver%'
    )
);
$$;

CREATE OR REPLACE FUNCTION public.admin_earnings_daily_series(
    p_start           timestamptz,
    p_end             timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS TABLE (day date, gbv numeric, trips bigint, net_revenue numeric)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT (ride_completed_at AT TIME ZONE 'UTC')::date AS day,
           COALESCE(SUM(total_fare::text::numeric), 0)    AS gbv,
           COUNT(*)                                       AS trips,
           COALESCE(SUM(admin_earnings::text::numeric), 0) AS net_revenue
    FROM rides
    WHERE status = 'completed'
      AND ride_completed_at >= p_start AND ride_completed_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
    GROUP BY 1
    ORDER BY 1;
$$;

CREATE OR REPLACE FUNCTION public.admin_earnings_refunds(
    p_start           timestamptz,
    p_end             timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT jsonb_build_object(
        'refund_amount', COALESCE(SUM(refund_amount::text::numeric), 0),
        'refund_count',  COUNT(*) FILTER (WHERE COALESCE(refund_amount, 0)::text::numeric > 0)
    )
    FROM disputes d
    WHERE d.resolved_at >= p_start AND d.resolved_at <= p_end
      AND (
        p_service_area_id IS NULL
        OR d.ride_id IN (
            SELECT r.id FROM rides r
            WHERE r.service_area_id::text = p_service_area_id
              AND (
                (r.status = 'completed' AND r.ride_completed_at >= p_start AND r.ride_completed_at <= p_end)
                OR (r.status = 'cancelled' AND r.created_at >= p_start AND r.created_at <= p_end)
              )
        )
      );
$$;

COMMENT ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) IS
    'Completed + cancelled ride aggregates for one /earnings/overview window.';
COMMENT ON FUNCTION public.admin_earnings_daily_series(timestamptz, timestamptz, text) IS
    'Per-day GBV/trips/net-revenue over completed rides for the /earnings/overview chart.';
COMMENT ON FUNCTION public.admin_earnings_refunds(timestamptz, timestamptz, text) IS
    'Refund $ + count from disputes resolved in a window (area-scoped via ride set).';

REVOKE EXECUTE ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.admin_earnings_daily_series(timestamptz, timestamptz, text) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.admin_earnings_refunds(timestamptz, timestamptz, text) FROM anon, authenticated;
