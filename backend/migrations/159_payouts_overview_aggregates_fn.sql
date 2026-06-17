-- 159_payouts_overview_aggregates_fn.sql
--
-- Purpose:
--   The admin /payouts/overview dashboard computed its all-time aggregates
--   (outstanding payable, stuck-payout queue, blocked-driver balance, and the
--   T4A year-to-date snapshot) by streaming the ENTIRE rides + payouts history
--   into Python via two `get_rows(..., limit=200000)` full-table scans, then
--   summing in a loop. This function does the same arithmetic in Postgres in a
--   single round-trip, so the backend no longer pulls hundreds of thousands of
--   rows over the wire to add them up.
--
--   The period-windowed, per-row metrics (daily series, failure reasons,
--   median time-to-payout, top / at-risk drivers) stay in Python — they need
--   the individual rows — but the endpoint now fetches only the rows inside the
--   selected window, not the whole table.
--
-- Semantics mirror the previous Python exactly:
--   * effective ride timestamp  = COALESCE(ride_completed_at, updated_at);
--     a completed ride with neither timestamp is still counted in the
--     cumulative "up to" sums (matched the old ""<=ts string comparison) but
--     excluded from the YTD T4A window (matched "">=year_start being false).
--   * effective payout state set for "paid or in flight" =
--     ('completed','pending','processing').
--   * money (FLOAT columns) is cast ::text::numeric before summing, so it
--     matches the old per-row Decimal(str(float)) summation to the cent — a
--     bare ::numeric would keep the float's binary rounding error.
--
-- Money-function safety: STABLE + SECURITY DEFINER + pinned search_path, read
-- only (no writes), callable by the backend service role only.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.admin_payouts_overview_aggregates(
--       timestamptz, timestamptz, timestamptz, timestamptz, text);
--   DROP INDEX IF EXISTS idx_rides_completed_driver;
--   DROP INDEX IF EXISTS idx_payouts_processed_at;
--   (idx_payouts_status_created is owned by migration 79 — do NOT drop it here.)
--
-- Note: the two CREATE INDEX statements use CONCURRENTLY because rides/payouts
-- are hot tables. CONCURRENTLY cannot run inside a transaction block; the
-- migration runner detects "CONCURRENTLY" in the file and applies it in
-- autocommit mode (same handling as 153/156).

-- Supporting indexes for the aggregate predicates + the windowed payout fetch.
-- The (status, created_at DESC) predicate is already covered by
-- idx_payouts_status_created from migration 79 — not recreated here.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_completed_driver
    ON public.rides (driver_id) WHERE status = 'completed';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_payouts_processed_at
    ON public.payouts (processed_at);

CREATE OR REPLACE FUNCTION public.admin_payouts_overview_aggregates(
    p_end             timestamptz,
    p_prev_end        timestamptz,
    p_year_start      timestamptz,
    p_stuck_before    timestamptz,
    p_service_area_id text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
WITH scoped_drivers AS (
    -- Resolve the area → driver-id set once (only when an area is selected) so
    -- the scope predicate isn't a repeated correlated subquery on drivers.
    SELECT id FROM drivers
    WHERE p_service_area_id IS NOT NULL AND service_area_id::text = p_service_area_id
),
scoped_rides AS (
    -- driver_earnings is FLOAT in the DB; cast via text so the value matches
    -- the old Python Decimal(str(float)) path exactly (float8::text uses the
    -- shortest round-trip representation, same as Python's str(float)). A bare
    -- ::numeric would carry the binary rounding error (12.2999999…) and drift
    -- from the previous totals.
    SELECT r.driver_id,
           r.driver_earnings::text::numeric AS earnings,
           COALESCE(r.ride_completed_at, r.updated_at) AS eff_ts
    FROM rides r
    WHERE r.status = 'completed'
      AND (p_service_area_id IS NULL OR r.driver_id IN (SELECT id FROM scoped_drivers))
),
scoped_payouts AS (
    -- payouts.amount is FLOAT — same ::text::numeric cast rationale as above.
    SELECT p.driver_id, p.amount::text::numeric AS amount, p.status, p.created_at
    FROM payouts p
    WHERE (p_service_area_id IS NULL OR p.driver_id IN (SELECT id FROM scoped_drivers))
),
blocked AS (
    SELECT d.id
    FROM drivers d
    WHERE d.stripe_payouts_enabled = false
      AND d.stripe_account_id IS NOT NULL
      AND (p_service_area_id IS NULL OR d.id IN (SELECT id FROM scoped_drivers))
),
ytd AS (
    -- T4A is per-PAYEE (driver); a completed ride with no driver_id is not a
    -- payee and must not form a (NULL, earned) group. The old Python skipped
    -- such rides ("if not did: continue"). The cumulative earned_up_to/
    -- outstanding sums above intentionally DO include null-driver rides.
    SELECT driver_id, COALESCE(SUM(earnings), 0) AS earned
    FROM scoped_rides
    WHERE eff_ts >= p_year_start AND driver_id IS NOT NULL
    GROUP BY driver_id
)
SELECT jsonb_build_object(
    'earned_up_to_end',
        COALESCE((SELECT SUM(earnings) FROM scoped_rides WHERE eff_ts IS NULL OR eff_ts <= p_end), 0),
    'earned_up_to_prev',
        COALESCE((SELECT SUM(earnings) FROM scoped_rides WHERE eff_ts IS NULL OR eff_ts <= p_prev_end), 0),
    'paid_up_to_end',
        COALESCE((SELECT SUM(amount) FROM scoped_payouts
                  WHERE status IN ('completed','pending','processing')
                    AND (created_at IS NULL OR created_at <= p_end)), 0),
    'paid_up_to_prev',
        COALESCE((SELECT SUM(amount) FROM scoped_payouts
                  WHERE status IN ('completed','pending','processing')
                    AND (created_at IS NULL OR created_at <= p_prev_end)), 0),
    'stuck_count',
        (SELECT COUNT(*) FROM scoped_payouts
         WHERE status IN ('pending','processing')
           AND (created_at IS NULL OR created_at < p_stuck_before)),
    'stuck_amount',
        COALESCE((SELECT SUM(amount) FROM scoped_payouts
                  WHERE status IN ('pending','processing')
                    AND (created_at IS NULL OR created_at < p_stuck_before)), 0),
    'blocked_count',
        (SELECT COUNT(*) FROM blocked),
    'blocked_outstanding',
        GREATEST(
            COALESCE((SELECT SUM(earnings) FROM scoped_rides WHERE driver_id IN (SELECT id FROM blocked)), 0)
            - COALESCE((SELECT SUM(amount) FROM scoped_payouts
                        WHERE driver_id IN (SELECT id FROM blocked)
                          AND status IN ('completed','pending','processing')), 0),
            0),
    't4a_under_500',  (SELECT COUNT(*) FROM ytd WHERE earned < 500),
    't4a_500_10k',    (SELECT COUNT(*) FROM ytd WHERE earned >= 500   AND earned < 10000),
    't4a_10k_30k',    (SELECT COUNT(*) FROM ytd WHERE earned >= 10000 AND earned < 30000),
    't4a_over_30k',   (SELECT COUNT(*) FROM ytd WHERE earned >= 30000),
    't4a_drivers_with_earnings', (SELECT COUNT(*) FROM ytd),
    't4a_ytd_gross',  COALESCE((SELECT SUM(earned) FROM ytd), 0)
);
$$;

COMMENT ON FUNCTION public.admin_payouts_overview_aggregates(
    timestamptz, timestamptz, timestamptz, timestamptz, text) IS
    'All-time payout/earnings aggregates for the admin /payouts/overview dashboard. '
    'Replaces two limit=200000 full-table scans. Read-only.';

-- Called from the backend (service role) only. The service role bypasses these
-- REVOKEs by design; this just stops a rider/driver/anon JWT from invoking an
-- RPC that exposes fleet-wide financial + T4A aggregates.
REVOKE EXECUTE ON FUNCTION public.admin_payouts_overview_aggregates(
    timestamptz, timestamptz, timestamptz, timestamptz, text) FROM anon, authenticated;
