-- 80_service_areas_vehicle_pricing.sql
--
-- Adds the missing `vehicle_pricing` JSONB column to `service_areas`.
--
-- Why this migration exists:
-- ── backend/routes/fares.py reads `service_areas.vehicle_pricing` as the
--    canonical source of per-area fare overrides (see _fares_for_location_impl).
-- ── The admin dashboard's Service Areas → Vehicle Pricing editor writes to
--    this column via PUT /admin/service-areas/{id}.
-- ── No prior migration ever created the column. Reads worked because Supabase
--    returns NULL for missing JSONB columns, but writes silently failed at
--    the DB layer ("database operation failed" → 503 in the admin UI).
--
-- Schema:
--   vehicle_pricing JSONB  (array of pricing rows keyed by vehicle type NAME)
--
-- Each row shape (enforced by application code, not the DB):
--   {
--     "vehicle_type":  string,   -- vehicle_types.name (NOT the id)
--     "base_fare":     number,
--     "per_km":        number,
--     "per_min":       number,
--     "min_fare":      number,
--     "booking_fee":   number
--   }
--
-- Rollback: ALTER TABLE service_areas DROP COLUMN IF EXISTS vehicle_pricing;
-- Safe — no other column or table references it.
--
-- Forward-compatible: adding a nullable JSONB column is non-blocking on
-- Postgres; existing rows get NULL, which fares.py treats as "no override"
-- and falls back to fare_configs. No backfill required.

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'service_areas' AND column_name = 'vehicle_pricing'
    ) THEN
        ALTER TABLE service_areas ADD COLUMN vehicle_pricing JSONB;
        COMMENT ON COLUMN service_areas.vehicle_pricing IS
            'Per-area fare overrides keyed by vehicle type name. See backend/routes/fares.py:_fares_for_location_impl for read semantics.';
    END IF;
END $$;
