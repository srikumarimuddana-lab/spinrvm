-- Migration 46: Add fee/tax columns and payment_method_id to rides table
-- These columns are populated at ride creation time by the POST /rides
-- endpoint and store the full fee/tax breakdown for each ride.
-- payment_method_id was defined in the Ride model but never migrated.

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS payment_method_id TEXT;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS airport_fee DECIMAL(10,2) DEFAULT 0;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS airport_zone_name TEXT;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS area_fees_breakdown JSONB DEFAULT '[]'::JSONB;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS area_fees_total DECIMAL(10,2) DEFAULT 0;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS tax_amount DECIMAL(10,2) DEFAULT 0;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS tax_breakdown JSONB DEFAULT '{}'::JSONB;

ALTER TABLE rides
  ADD COLUMN IF NOT EXISTS grand_total DECIMAL(10,2) DEFAULT 0;

COMMENT ON COLUMN rides.airport_fee IS 'Airport surcharge applied when pickup/dropoff/stop is in an airport zone.';
COMMENT ON COLUMN rides.airport_zone_name IS 'Name of the airport zone that triggered the surcharge.';
COMMENT ON COLUMN rides.area_fees_breakdown IS 'Itemised area fees as JSON array: [{id, name, type, calculated_value}, ...]';
COMMENT ON COLUMN rides.area_fees_total IS 'Sum of all area-specific fees.';
COMMENT ON COLUMN rides.tax_amount IS 'Total tax charged on the ride.';
COMMENT ON COLUMN rides.tax_breakdown IS 'Per-tax breakdown: {tax_name: {rate, amount}, ...}';
COMMENT ON COLUMN rides.grand_total IS 'total_fare + area_fees_total + tax_amount — the amount actually charged to the rider.';
