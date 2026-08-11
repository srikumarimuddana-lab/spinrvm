-- 303_payouts_overview_ytd_exclude_legacy.sql
--
-- Purpose:
--   P0-B (docs/audit/2026-08-11-driver-rider-migration-audit.md):
--   admin_payouts_overview_aggregates (migration 159) powers the admin
--   /payouts/overview dashboard, including its T4A year-to-date snapshot
--   (t4a_under_500 .. t4a_over_30k, t4a_ytd_gross, t4a_drivers_with_earnings).
--   That snapshot summed `scoped_rides` with no legacy-import exclusion, so
--   it can disagree with the actual T4A slips issued by
--   utils/t4a_annual_job.py (which correctly excludes legacy-imported rides
--   the same way every other driver-facing earnings surface does).
--
--   IMPORTANT — the other aggregates in this function (earned_up_to_end,
--   earned_up_to_prev, blocked_outstanding) are left UNCHANGED and must stay
--   that way: legacy-imported rides and their offsetting `payouts` rows
--   (payout_type='legacy_import', written by booking_import_service.py) are
--   both included together in those sums, so they already net to the
--   correct outstanding-payable figure. Per utils/legacy_rides.py's own
--   module docstring: "every caller must use both helpers together" —
--   filtering only the rides half here without also filtering the payouts
--   half would silently move a driver's reported payable balance. Only the
--   YTD/T4A snapshot (which has no payout-pairing concept — it counts gross
--   ride earnings per calendar year, not a running balance) gets the
--   exclusion.
--
--   This migration deliberately does NOT reuse utils/legacy_rides.py's
--   `legacy_import_metadata IS NULL` predicate. `rides.legacy_import_metadata`
--   is `NOT NULL DEFAULT '{}'::jsonb` (migration 268) — no row can ever be
--   SQL NULL there, so `IS NULL` matches zero rows, always (the ytd CTE would
--   be empty for every driver, hiding real T4A data instead of just excluding
--   legacy rows). See ACTION_ITEMS.md's EXCLUDE_LEGACY_RIDES entry — this may
--   be a live, separate, more severe pre-existing bug across 9+ other call
--   sites; not fixed here (out of scope for P0-B, no live DB to verify
--   against). This migration uses the actually-correct predicate instead:
--   `legacy_import_metadata = '{}'::jsonb`.
--
-- Same CREATE OR REPLACE target as 159; this is the first amendment.
--
-- Money-function safety: unchanged from 159 — STABLE, SECURITY DEFINER,
-- pinned search_path, read-only, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   Re-run 159_payouts_overview_aggregates_fn.sql's CREATE OR REPLACE
--   FUNCTION body (restores the unfiltered YTD snapshot). No new
--   index/column to drop — legacy_import_metadata already exists on rides
--   (migration 268).

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
    --
    -- legacy_import_metadata is carried through (not filtered here) because
    -- earned_up_to_end/prev and blocked_outstanding below intentionally
    -- include legacy-imported rides paired with their offsetting payouts —
    -- see this migration's header comment. Only the ytd CTE filters on it.
    SELECT r.driver_id,
           r.driver_earnings::text::numeric AS earnings,
           COALESCE(r.ride_completed_at, r.updated_at) AS eff_ts,
           r.legacy_import_metadata
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
    --
    -- P0-B: excludes legacy-imported rides so this snapshot matches the
    -- actual T4A slips utils/t4a_annual_job.py issues (which are correct).
    SELECT driver_id, COALESCE(SUM(earnings), 0) AS earned
    FROM scoped_rides
    WHERE eff_ts >= p_year_start AND driver_id IS NOT NULL AND legacy_import_metadata = '{}'::jsonb
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
    'T4A YTD snapshot excludes legacy-imported rides (P0-B, '
    'docs/audit/2026-08-11-driver-rider-migration-audit.md); the paired '
    'earned/outstanding sums intentionally do not. Replaces two limit=200000 full-table scans. Read-only.';

-- Called from the backend (service role) only. The service role bypasses these
-- REVOKEs by design; this just stops a rider/driver/anon JWT from invoking an
-- RPC that exposes fleet-wide financial + T4A aggregates.
REVOKE EXECUTE ON FUNCTION public.admin_payouts_overview_aggregates(
    timestamptz, timestamptz, timestamptz, timestamptz, text) FROM anon, authenticated;
