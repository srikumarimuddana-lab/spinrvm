-- 55_drivers_dispatch_composite_index.sql
-- Add a partial composite index for the driver-candidate dispatch query.
-- The query filters on (is_online=true, is_available=true, vehicle_type_id=?).
-- Without this index, every dispatch decision is a sequential scan on `drivers`
-- that grows linearly with fleet size and breaches the 2s P95 SLA past ~500 drivers.
--
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_online_available_type;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_online_available_type
    ON drivers (is_online, is_available, vehicle_type_id)
    WHERE is_online = true AND is_available = true;
