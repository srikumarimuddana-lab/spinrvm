-- 302_ride_money_rollup_exclude_legacy.sql
--
-- Purpose:
--   P0-B (docs/audit/2026-08-11-driver-rider-migration-audit.md):
--   admin_ride_money_rollup (migration 161) powers GET /admin/stats,
--   GET /admin/rides/financials and GET /admin/rides/earnings, and sums
--   every status='completed' ride with no legacy-import exclusion. Every
--   driver-facing earnings/statement/T4A surface is intended to exclude
--   legacy-imported rides (utils/legacy_rides.py's EXCLUDE_LEGACY_RIDES),
--   because booking_import_service.py pairs each imported ride's earnings
--   with an offsetting `payouts` row so the driver's payable BALANCE is
--   unaffected — but the imported ride's fare/earnings totals are money the
--   PREVIOUS app already paid out, not new Spinr revenue. This admin
--   aggregate had no such exclusion, so it overstates gross revenue/
--   earnings for any report window containing legacy-imported rides'
--   original (legacy) ride_completed_at dates.
--
--   IMPORTANT — this migration deliberately does NOT reuse
--   utils/legacy_rides.py's `legacy_import_metadata IS NULL` predicate.
--   `rides.legacy_import_metadata` is `NOT NULL DEFAULT '{}'::jsonb`
--   (migration 268) — no row can ever be SQL NULL there, so `IS NULL`
--   matches zero rows, always (excludes every ride, not just legacy ones).
--   See ACTION_ITEMS.md's EXCLUDE_LEGACY_RIDES entry — this may be a live,
--   separate, more severe pre-existing bug across 9+ other call sites; not
--   fixed here (out of scope for P0-B, no live DB to verify against). This
--   migration uses the actually-correct predicate instead:
--   `legacy_import_metadata = '{}'::jsonb` (the column's own default for a
--   non-imported row, per migration 268's own "'{}' means not imported"
--   comment and utils/legacy_rides.py's `is_legacy_ride()`'s Python-truthy
--   check, which the same empty object satisfies).
--
--   Unlike admin_payouts_overview_aggregates (see 303), this function has no
--   payout-pairing concept to preserve — it purely sums ride money — so the
--   exclusion is unconditional.
--
-- Same CREATE OR REPLACE target as 161; this is the first amendment.
--
-- Money-function safety: unchanged from 161 — read-only, STABLE,
-- SECURITY DEFINER, pinned search_path, EXECUTE revoked from anon/authenticated.
--
-- Rollback:
--   Re-run 161_ride_money_rollup_fn.sql's CREATE OR REPLACE FUNCTION body
--   (restores the unfiltered version). No new index/column to drop.

CREATE OR REPLACE FUNCTION public.admin_ride_money_rollup(
    p_start timestamptz,
    p_end   timestamptz DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    WITH c AS (
        SELECT
            COALESCE(total_fare, 0)::text::numeric      AS total_fare,
            COALESCE(tip_amount, 0)::text::numeric      AS tip_amount,
            COALESCE(tax_amount, 0)::text::numeric      AS tax_amount,
            COALESCE(discount_amount, 0)::text::numeric AS discount_amount,
            COALESCE(area_fees_total, 0)::text::numeric AS area_fees_total,
            COALESCE(admin_earnings, 0)::text::numeric  AS admin_earnings,
            COALESCE(driver_earnings, 0)::text::numeric AS driver_earnings,
            COALESCE(base_fare, 0)::text::numeric
              + COALESCE(distance_fare, 0)::text::numeric
              + COALESCE(time_fare, 0)::text::numeric   AS driver_revenue,
            COALESCE(grand_total, total_fare, 0)::text::numeric AS rider_paid
        FROM rides
        WHERE status = 'completed'
          AND ride_completed_at >= p_start
          AND (p_end IS NULL OR ride_completed_at < p_end)
          AND legacy_import_metadata = '{}'::jsonb
    )
    SELECT jsonb_build_object(
        'completed_count',     (SELECT COUNT(*) FROM c),
        'sum_total_fare',      COALESCE((SELECT SUM(total_fare) FROM c), 0),
        'sum_tip',             COALESCE((SELECT SUM(tip_amount) FROM c), 0),
        'sum_tax',             COALESCE((SELECT SUM(tax_amount) FROM c), 0),
        'sum_discount',        COALESCE((SELECT SUM(discount_amount) FROM c), 0),
        'sum_area_fees',       COALESCE((SELECT SUM(area_fees_total) FROM c), 0),
        'sum_admin_earnings',  COALESCE((SELECT SUM(admin_earnings) FROM c), 0),
        'sum_driver_earnings', COALESCE((SELECT SUM(driver_earnings) FROM c), 0),
        'sum_driver_revenue',  COALESCE((SELECT SUM(driver_revenue) FROM c), 0),
        'sum_rider_paid',      COALESCE((SELECT SUM(rider_paid) FROM c), 0)
    );
$$;

COMMENT ON FUNCTION public.admin_ride_money_rollup(timestamptz, timestamptz) IS
    'Completed-ride money sums for a [start,end) window (end NULL = open), '
    'excluding legacy-imported rides (P0-B, docs/audit/2026-08-11-driver-rider-migration-audit.md). '
    'Replaces row-fetch+Python-sum in /stats, /rides/stats, /rides/financials.';

REVOKE EXECUTE ON FUNCTION public.admin_ride_money_rollup(timestamptz, timestamptz)
    FROM anon, authenticated;
