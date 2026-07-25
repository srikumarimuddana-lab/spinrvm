-- 250_period1_distance_accumulator.sql
-- Scalar accumulator for Period-1 (online-available, no ride) driven distance.
--
-- Period 1 (TNC contingent liability) is not ride-scoped, so there is no ride
-- breadcrumb trail to measure. To audit deadhead mileage for the SGI /
-- Saskatchewan Transportation Act commercial-coverage audit WITHOUT storing a
-- continuous off-ride location trail (PIPEDA data-minimization; Spinr is not a
-- driver-surveillance platform), we keep only a RUNNING SCALAR: the incremental
-- distance summed from the location batches the app already sends while online
-- with no active ride. No coordinates are stored — only the kilometre total and
-- when the current span started. The finalizer loop drains it into the
-- append-only driver_period_distances audit (period=1) when the span ends.
--
-- Off by default: period1_distance_tracking_enabled must be turned on
-- deliberately (an owner/legal decision, since it measures a contractor's
-- between-rides movement).
--
-- Rollback:
--   ALTER TABLE drivers  DROP COLUMN IF EXISTS period1_accum_km;
--   ALTER TABLE drivers  DROP COLUMN IF EXISTS period1_accum_since;
--   ALTER TABLE settings DROP COLUMN IF EXISTS period1_distance_tracking_enabled;
--
-- Forward-compatible: additive nullable/defaulted columns; metadata-only on
-- Postgres 11+ (no table rewrite). Old backend ignores the new columns.

ALTER TABLE drivers
    ADD COLUMN IF NOT EXISTS period1_accum_km    numeric(10, 3) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS period1_accum_since timestamptz;

ALTER TABLE settings
    ADD COLUMN IF NOT EXISTS period1_distance_tracking_enabled boolean NOT NULL DEFAULT false;

NOTIFY pgrst, 'reload schema';
