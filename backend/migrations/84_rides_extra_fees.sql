-- 84_rides_extra_fees.sql
--
-- Purpose: Add extra_fees JSONB to rides to snapshot whatever dynamic
--          fees were configured on the service area at booking time.
--          This replaces the previously-planned platform_fee / city_fee
--          scalar columns — any fee the admin defines (platform_fee,
--          city_fee, environmental_levy, etc.) is stored here without
--          requiring a new migration.
--
-- Rollback: ALTER TABLE rides DROP COLUMN IF EXISTS extra_fees;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS extra_fees JSONB DEFAULT '{}';

COMMENT ON COLUMN rides.extra_fees IS
    'Snapshot of service_areas.fees at booking time. Keys are fee names '
    '(e.g. platform_fee, city_fee); values are NUMERIC strings. '
    'Sum of all values flows to admin_earnings.';
