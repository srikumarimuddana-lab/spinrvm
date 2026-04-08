-- Migration: Add sub-region support to service_areas
-- Adds parent_service_area_id to allow airport zones as sub-regions of a parent area

ALTER TABLE service_areas
  ADD COLUMN IF NOT EXISTS parent_service_area_id TEXT REFERENCES service_areas(id) ON DELETE SET NULL;

-- Index for fast lookup of sub-regions by parent
CREATE INDEX IF NOT EXISTS idx_service_areas_parent ON service_areas(parent_service_area_id)
  WHERE parent_service_area_id IS NOT NULL;

COMMENT ON COLUMN service_areas.parent_service_area_id IS
  'If set, this area is a sub-region (e.g. airport zone) of the parent service area.';
