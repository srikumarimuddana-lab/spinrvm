-- Migration 185: per-service-area vehicle cascade map
-- Rollback: ALTER TABLE service_areas DROP COLUMN IF EXISTS vehicle_cascade_map;
--
-- Stores an ordered list of cascade rules for dispatch.
-- Shape: [{"from": "<vehicle_type_id>", "to": ["<vehicle_type_id>", ...]}]
-- When a ride requests vehicle type X and no driver is found, the dispatch
-- loop re-queries for any "to" type IDs mapped from X for this area.
-- Empty array (default) means no cascading — exact match only.

ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS vehicle_cascade_map JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN service_areas.vehicle_cascade_map IS
    'Ordered dispatch cascade rules [{from: uuid, to: [uuid]}]. '
    'When no driver matches the requested vehicle type, dispatch retries '
    'with the to[] types (upgrade-only). Empty array disables cascading.';
