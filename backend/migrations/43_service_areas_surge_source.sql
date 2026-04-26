-- Migration 43: service_areas.surge_source
--
-- surge_engine.py already reads ``surge_source`` ("auto" | "manual")
-- to decide whether to overwrite a manually-set surge multiplier,
-- but the column never existed in the schema. Defaulting to "auto"
-- via .get() meant every area was considered auto-managed, so an
-- operator's manual surge toggle could be silently overwritten on
-- the next engine tick.
--
-- Add the column with a safe default. Existing rows stay on "auto"
-- so engine behaviour is unchanged for anything admins haven't
-- touched. When admins edit surge via Service Areas → General, we
-- flip it to "manual" so the engine leaves the area alone.

ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS surge_source TEXT NOT NULL DEFAULT 'auto';

COMMENT ON COLUMN service_areas.surge_source IS
    '"auto" = managed by surge_engine; "manual" = admin override, engine will not touch.';
