-- Read-only reconciliation: driver payouts vs. rides whose fare was never collected.
--
-- Context (docs/change-log/2026-09-20-uncollected-rides-not-payable.md):
-- until that change, driver-payable money summed every status='completed' ride
-- with no payment_status predicate, so a completed ride whose card charge
-- failed (payment_status='failed', 'pending', 'processing', ...) still counted
-- toward payable_balance — and the weekly auto_payout batch / instant payout
-- sent it through a real Stripe Transfer. This report shows, per driver, how
-- much of what has ALREADY been paid out is not backed by collected fares.
--
-- REPORT ONLY. Nothing here writes, and no automatic claw-back exists or
-- should be built from this: a driver still drove the trip, and whether Spinr
-- recovers an uncollected fare from the rider (payment_retry / dunning) or
-- absorbs it is a per-case business decision. Hand the output to finance.
--
-- Run against a read replica / with a read-only role:
--   psql "$DATABASE_URL" -f backend/scripts/reconcile_uncollected_ride_payouts.sql
--
-- Formula mirrors routes/drivers/earnings.get_driver_balance and
-- utils/auto_payout._balance_from_rows as closely as SQL allows:
--   * ride income  = COALESCE(driver_earnings, base+distance+time+tip)
--   * ride tax     = tax_amount (the Python fallback that scans
--                    fare_breakdown_snapshot lines is not reproduced here)
--   * legacy-imported rides (legacy_import_metadata <> '{}') excluded, and the
--     three payout types that are not money out of this balance
--     ('stripe_sync', 'legacy_outstanding_correction', 'legacy_import') excluded
--   * payouts with status 'reversed' / 'failed' are not money out
--
-- Columns:
--   uncollected_rides       completed, non-legacy rides NOT in the collected set
--   uncollected_payable     income + tax those rides contributed before the fix
--   collected_payable       income + tax + incentives from rides that ARE collected
--   other_earnings          driver bonuses + cancellation fees (not ride-gated)
--   payouts_sent            money that actually left (pending/held included,
--                           same as the balance formula's "money out")
--   overpaid_exposure       max(0, payouts_sent - collected_payable - other_earnings)
--                           i.e. the part of what was paid that only uncollected
--                           fares could have funded. 0 means no exposure even
--                           though uncollected rides exist (the driver had
--                           enough collected earnings to cover every payout).

WITH collected_statuses AS (
    -- Keep in step with backend/utils/payment_collection.py
    SELECT unnest(ARRAY['paid', 'waived_admin', 'refunded', 'partially_refunded', 'disputed', 'dispute_lost']) AS s
),
completed AS (
    SELECT
        r.id,
        r.driver_id,
        r.payment_status,
        COALESCE(
            r.driver_earnings,
            COALESCE(r.base_fare, 0) + COALESCE(r.distance_fare, 0)
                + COALESCE(r.time_fare, 0) + COALESCE(r.tip_amount, 0)
        )::numeric AS income,
        COALESCE(r.tax_amount, 0)::numeric AS tax,
        (r.payment_status IN (SELECT s FROM collected_statuses)) AS is_collected
    FROM rides r
    WHERE r.status = 'completed'
      AND r.driver_id IS NOT NULL
      AND COALESCE(r.legacy_import_metadata, '{}'::jsonb) = '{}'::jsonb
),
incentives AS (
    SELECT c.driver_id, SUM(COALESCE(i.bonus_amount, 0))::numeric AS incentives
    FROM ride_incentive_claims i
    JOIN completed c ON c.id = i.ride_id
    WHERE c.is_collected
    GROUP BY c.driver_id
),
ride_sums AS (
    SELECT
        driver_id,
        COUNT(*) FILTER (WHERE NOT is_collected)                      AS uncollected_rides,
        SUM(income + tax) FILTER (WHERE NOT is_collected)             AS uncollected_payable,
        SUM(income + tax) FILTER (WHERE is_collected)                 AS collected_ride_payable
    FROM completed
    GROUP BY driver_id
),
other_earnings AS (
    SELECT driver_id, SUM(amt)::numeric AS other_earnings
    FROM (
        SELECT driver_id, COALESCE(amount, 0)::numeric AS amt FROM driver_bonuses
        UNION ALL
        SELECT driver_id, COALESCE(cancellation_fee_driver, 0)::numeric
        FROM rides WHERE status = 'cancelled' AND driver_id IS NOT NULL
    ) x
    GROUP BY driver_id
),
payouts_sent AS (
    SELECT driver_id, SUM(COALESCE(amount, 0))::numeric AS payouts_sent
    FROM payouts
    WHERE lower(COALESCE(status, '')) NOT IN ('reversed', 'failed')
      AND COALESCE(payout_type, '') NOT IN ('stripe_sync', 'legacy_outstanding_correction', 'legacy_import')
    GROUP BY driver_id
)
SELECT
    rs.driver_id,
    d.user_id,
    rs.uncollected_rides,
    ROUND(COALESCE(rs.uncollected_payable, 0), 2)                                   AS uncollected_payable,
    ROUND(COALESCE(rs.collected_ride_payable, 0) + COALESCE(i.incentives, 0), 2)     AS collected_payable,
    ROUND(COALESCE(o.other_earnings, 0), 2)                                          AS other_earnings,
    ROUND(COALESCE(p.payouts_sent, 0), 2)                                            AS payouts_sent,
    ROUND(GREATEST(
        COALESCE(p.payouts_sent, 0)
        - COALESCE(rs.collected_ride_payable, 0) - COALESCE(i.incentives, 0)
        - COALESCE(o.other_earnings, 0),
        0), 2)                                                                       AS overpaid_exposure
FROM ride_sums rs
LEFT JOIN drivers d        ON d.id = rs.driver_id
LEFT JOIN incentives i     ON i.driver_id = rs.driver_id
LEFT JOIN other_earnings o ON o.driver_id = rs.driver_id
LEFT JOIN payouts_sent p   ON p.driver_id = rs.driver_id
WHERE rs.uncollected_rides > 0
ORDER BY overpaid_exposure DESC, uncollected_payable DESC;

-- Fleet-wide roll-up (same CTEs, one row). Uncomment to run separately.
-- SELECT COUNT(*) AS drivers_with_uncollected_rides,
--        SUM(uncollected_rides) AS uncollected_rides,
--        SUM(uncollected_payable) AS uncollected_payable,
--        SUM(overpaid_exposure) AS overpaid_exposure
-- FROM ( <the SELECT above> ) t;
