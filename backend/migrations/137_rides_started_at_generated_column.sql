-- 137_rides_started_at_generated_column.sql
-- Add rides.started_at as a Postgres GENERATED ALWAYS AS column that mirrors
-- ride_started_at. Something in the stack (a Supabase stored function, view,
-- or PostgREST schema cache not tracked in these migrations) is querying
-- rides.started_at and failing with Postgres 42703. Extensive search of the
-- Python backend found no explicit reference; this migration resolves the
-- column-not-found error at the schema level while the querying code is
-- identified.
--
-- Type: TIMESTAMPTZ GENERATED ALWAYS AS (ride_started_at) STORED
--   - Always equal to ride_started_at; no write overhead beyond the source column.
--   - Cannot be set directly in INSERT/UPDATE (correct — only ride_started_at
--     should be written).
--   - NULL when ride_started_at is NULL (i.e. before the driver starts the trip).
--
-- Rollback: ALTER TABLE public.rides DROP COLUMN IF EXISTS started_at;

ALTER TABLE public.rides
    ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ
        GENERATED ALWAYS AS (ride_started_at) STORED;

COMMENT ON COLUMN public.rides.started_at IS
    'Read-only alias for ride_started_at (generated column). Exists to satisfy '
    'unidentified legacy query or Supabase object that references rides.started_at. '
    'Write ride_started_at directly; this column updates automatically.';
