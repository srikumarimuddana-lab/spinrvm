-- 140: vehicle_types.marker_variant — which map marker icon the rider app
-- renders for drivers of this vehicle type. Configurable from the admin
-- dashboard (Vehicle Types page); the rider app falls back to name matching
-- when the column is absent or 'standard'.
--
-- Allowed values: 'standard' (white sedan), 'xl' (white van), 'premium'
-- (black SUV). Adding a marker later requires dropping/recreating the CHECK
-- in a new migration.
--
-- Safe under production traffic: ADD COLUMN with a constant DEFAULT is
-- metadata-only on Postgres 11+; the backfill UPDATEs touch a handful of
-- rows (vehicle_types is a tiny config table).
--
-- Rollback plan:
--   ALTER TABLE vehicle_types DROP CONSTRAINT vehicle_types_marker_variant_check;
--   ALTER TABLE vehicle_types DROP COLUMN marker_variant;

ALTER TABLE vehicle_types
    ADD COLUMN IF NOT EXISTS marker_variant TEXT NOT NULL DEFAULT 'standard';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'vehicle_types_marker_variant_check'
    ) THEN
        ALTER TABLE vehicle_types
            ADD CONSTRAINT vehicle_types_marker_variant_check
            CHECK (marker_variant IN ('standard', 'xl', 'premium'));
    END IF;
END $$;

-- Backfill from existing names so current types render the right marker
-- without an admin touching anything (mirrors the rider app's previous
-- hardcoded name matching).
UPDATE vehicle_types SET marker_variant = 'xl'
    WHERE marker_variant = 'standard'
      AND (lower(name) LIKE '%xl%' OR lower(name) LIKE '%van%');
UPDATE vehicle_types SET marker_variant = 'premium'
    WHERE marker_variant = 'standard'
      AND (lower(name) LIKE '%premium%' OR lower(name) LIKE '%lux%');
