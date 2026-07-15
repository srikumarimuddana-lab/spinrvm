-- 227_earnings_overview_funnel_agg.sql
--
-- (Replaces this branch's earlier 227_ride_funnel_agg_fn.sql, which was never
-- merged or applied anywhere — deploys run migrations from main only, so the
-- rename + rewrite is safe. A defensive DROP below covers any dev DB that ran
-- the old file.)
--
-- Purpose:
--   Ops-funnel counts for GET /api/admin/earnings/overview (Operational
--   health), folded INTO admin_earnings_overview_agg instead of a separate
--   function, so the endpoint keeps its 4 RPCs (no extra rides window scans)
--   and the cancel-attribution rule lives in exactly one place for this page.
--
-- Changes to admin_earnings_overview_agg (re-forked from migration 163, the
-- current authoritative definition; admin_earnings_daily_series and
-- admin_earnings_refunds are untouched):
--
--   1. Cancel attribution now uses the STRUCTURED rides.cancelled_by column
--      (migration 38: rider | driver | admin | system — written by every
--      cancel path) instead of LIKE-matching free-text cancellation_reason.
--      The old heuristic counted system auto-cancels ("No nearby drivers
--      found. Please try again." — matching.py / stuck_ride_sweeper.py) as
--      DRIVER cancels because the string contains 'drivers'. cancelled_by
--      values outside rider/driver (admin, system) bucket as neither, so the
--      endpoint's existing residual (count − rider − driver) becomes the
--      correct system/other count. Legacy rows with NULL cancelled_by fall
--      back to a GUARDED reason heuristic — no-driver checked BEFORE driver,
--      same as admin_cancellation_breakdown (migration 165).
--
--   2. New cohort CTE (rides CREATED in [start, end], any status) feeding
--      funnel keys:
--        fn_requested             — rides created in the window
--        fn_reached_searching     — rides that actually entered dispatch:
--                                   instant rides (is_scheduled = false, which
--                                   are created directly in 'searching') plus
--                                   scheduled rides the dispatch loop flipped
--                                   (scheduled_dispatched = true, migration
--                                   114). A scheduled ride cancelled before
--                                   dispatch is correctly NOT counted — the
--                                   old status <> 'scheduled' proxy counted it.
--        fn_completed             — cohort rides that completed (travelled)
--        fn_cancelled_after_start — cancelled with ride_started_at set (per
--                                   the state machine this should be ~0;
--                                   surfaced so anomalies are visible)
--        fn_price_searches        — fare-estimate lookups (migration 226) in
--                                   the window: the funnel top, before any
--                                   ride row exists
--      The funnel's rider/driver/system cancel splits are NOT duplicated
--      here — the endpoint reuses cx_rider_cancels / cx_driver_cancels /
--      the residual from this same result.
--
--   NOTE the funnel is a created_at cohort, so fn_completed differs from the
--   completion-date 'trips' key above it. Windows containing future-dated
--   scheduled bookings show them in fn_requested with no downstream progress
--   yet — the UI caption discloses this.
--
-- Indexes: rides(created_at) exists (migrations 50/120); price_searches
-- indexes ship in 226. No new index.
--
-- Function safety: read-only, STABLE, SECURITY DEFINER, pinned search_path,
-- EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   Re-apply migration 163's admin_earnings_overview_agg definition verbatim
--   (drops the funnel keys and restores reason-string attribution).

-- Defensive: remove the branch-local function the earlier version of this
-- migration created (no-op on any DB that never ran it).
DROP FUNCTION IF EXISTS public.admin_ride_funnel_agg(timestamptz, timestamptz, text);

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
cohort AS (
    -- Rides REQUESTED in the window (created_at cohort, any status) — feeds
    -- both the cancelled aggregates below (same window semantics as the old
    -- standalone cancelled CTE) and the funnel keys.
    SELECT status, cancelled_by, cancellation_reason, cancellation_fee_admin,
           ride_started_at, is_scheduled, scheduled_dispatched
    FROM rides
    WHERE created_at >= p_start AND created_at <= p_end
      AND (p_service_area_id IS NULL OR service_area_id::text = p_service_area_id)
),
cancelled AS (
    SELECT cancellation_fee_admin, ride_started_at,
           CASE
               WHEN cancelled_by IN ('rider', 'driver') THEN cancelled_by
               WHEN cancelled_by IS NOT NULL THEN 'system'  -- admin / system / other
               -- Legacy NULL cancelled_by: guarded reason heuristic. No-driver
               -- MUST be checked before '%driver%' ('No nearby drivers found'
               -- contains 'drivers') — mirrors migration 165.
               WHEN lower(COALESCE(cancellation_reason, '')) LIKE '%no nearby driver%'
                 OR lower(COALESCE(cancellation_reason, '')) LIKE '%no driver%' THEN 'system'
               WHEN lower(COALESCE(cancellation_reason, '')) LIKE '%driver%' THEN 'driver'
               WHEN lower(COALESCE(cancellation_reason, '')) LIKE '%rider%'
                 OR lower(COALESCE(cancellation_reason, '')) LIKE '%user%' THEN 'rider'
               ELSE 'system'
           END AS cancel_party
    FROM cohort
    WHERE status = 'cancelled'
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
    'cx_rider_cancels',  (SELECT COUNT(*) FROM cancelled WHERE cancel_party = 'rider'),
    'cx_driver_cancels', (SELECT COUNT(*) FROM cancelled WHERE cancel_party = 'driver'),
    'fn_requested',         (SELECT COUNT(*) FROM cohort),
    'fn_reached_searching', (
        SELECT COUNT(*) FROM cohort
        WHERE COALESCE(is_scheduled, false) = false
           OR COALESCE(scheduled_dispatched, false) = true
    ),
    'fn_completed',             (SELECT COUNT(*) FROM cohort WHERE status = 'completed'),
    'fn_cancelled_after_start', (SELECT COUNT(*) FROM cancelled WHERE ride_started_at IS NOT NULL),
    'fn_price_searches',        (
        SELECT COUNT(*) FROM price_searches
        WHERE created_at >= p_start AND created_at <= p_end
          AND (p_service_area_id IS NULL OR service_area_id = p_service_area_id)
    )
);
$$;

COMMENT ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) IS
    'Completed + cancelled ride aggregates plus ops-funnel counts (price searches → requested → reached searching → travelled → cancels, created_at cohort) for one /earnings/overview window. Cancel attribution via rides.cancelled_by (reason-string fallback for legacy NULLs).';

REVOKE EXECUTE ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) FROM anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.admin_earnings_overview_agg(timestamptz, timestamptz, text) TO service_role;

NOTIFY pgrst, 'reload schema';
