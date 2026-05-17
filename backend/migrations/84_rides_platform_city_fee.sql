-- 84_rides_platform_city_fee.sql
--
-- Purpose: Add platform_fee and city_fee columns to the rides table so that
--          fees configured on service_areas are stored at ride creation and
--          appear in receipts, invoices, and admin earnings reports.
--
--          Both fees flow to admin_earnings (not driver_earnings).
--          NULL means the area had no fee configured at the time of booking.
--
-- Rollback:
--   ALTER TABLE rides DROP COLUMN IF EXISTS platform_fee;
--   ALTER TABLE rides DROP COLUMN IF EXISTS city_fee;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS platform_fee NUMERIC(10, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS city_fee     NUMERIC(10, 2) DEFAULT 0;
