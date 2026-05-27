-- 104_rides_driver_earnings_snapshot.sql
-- Adds a JSONB column to store the driver's earnings breakdown at completion time.
-- This is the source of truth for what the driver earned, avoiding recomputation drift.
--
-- Rollback: ALTER TABLE rides DROP COLUMN IF EXISTS driver_earnings_snapshot;

ALTER TABLE rides ADD COLUMN IF NOT EXISTS driver_earnings_snapshot JSONB;

COMMENT ON COLUMN rides.driver_earnings_snapshot IS
  'Frozen driver earnings breakdown computed at ride completion. '
  'Keys: fare, tip, incentive, tax, cancel_fee, total, lines[].';

-- Backfill existing completed rides from current columns
UPDATE rides
SET driver_earnings_snapshot = jsonb_build_object(
  'fare', COALESCE(base_fare, 0) + COALESCE(distance_fare, 0) + COALESCE(time_fare, 0),
  'tip', COALESCE(tip_amount, 0),
  'tax', COALESCE(tax_amount, 0),
  'cancel_fee', COALESCE(cancellation_fee_driver, 0),
  'incentive', 0,
  'total', COALESCE(base_fare, 0) + COALESCE(distance_fare, 0) + COALESCE(time_fare, 0)
           + COALESCE(tip_amount, 0) + COALESCE(tax_amount, 0) + COALESCE(cancellation_fee_driver, 0),
  'lines', jsonb_build_array(
    jsonb_build_object('label', 'Ride Fare', 'amount', COALESCE(base_fare, 0) + COALESCE(distance_fare, 0) + COALESCE(time_fare, 0), 'type', 'fare'),
    jsonb_build_object('label', 'Tip', 'amount', COALESCE(tip_amount, 0), 'type', 'tip'),
    jsonb_build_object('label', 'Tax', 'amount', COALESCE(tax_amount, 0), 'type', 'tax')
  )
)
WHERE status = 'completed' AND driver_earnings_snapshot IS NULL;

-- Fix double-counted driver_earnings for existing rides
UPDATE rides
SET driver_earnings = COALESCE(base_fare, 0) + COALESCE(distance_fare, 0) + COALESCE(time_fare, 0) + COALESCE(tip_amount, 0)
WHERE status = 'completed'
  AND tip_amount > 0
  AND driver_earnings > COALESCE(base_fare, 0) + COALESCE(distance_fare, 0) + COALESCE(time_fare, 0) + COALESCE(tip_amount, 0) + 0.01;
