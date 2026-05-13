-- Rollback: ALTER TABLE rides DROP COLUMN IF EXISTS cancellation_fee_admin;
--           ALTER TABLE rides DROP COLUMN IF EXISTS cancellation_fee_driver;
--
-- These columns exist in supabase_schema.sql (the initial hand-applied schema)
-- but were never added to the numbered migration sequence. Any environment
-- bootstrapped with migrate.py alone (CI, staging, new deployments) is missing
-- them. The rider-cancel endpoint at /rides/{id}/cancel writes both fields in
-- _base_update, so when the columns are absent the PostgREST 400 propagates as
-- a DatabaseError → HTTP 500 → silent cancel failure in the rider app.
--
-- ADD COLUMN IF NOT EXISTS is safe to run against the Supabase project that was
-- initialised from supabase_schema.sql — it will be a no-op for those columns.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancellation_fee_admin  FLOAT NOT NULL DEFAULT 0;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancellation_fee_driver FLOAT NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_rides_cancellation_fee
    ON rides (cancellation_fee_admin, cancellation_fee_driver)
    WHERE cancellation_fee_admin > 0 OR cancellation_fee_driver > 0;
