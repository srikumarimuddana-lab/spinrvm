-- Add timezone column to service_areas so earnings date boundaries are derived
-- from each service area's local time rather than a hardcoded UTC offset.
--
-- Rollback: ALTER TABLE service_areas DROP COLUMN IF EXISTS timezone;

ALTER TABLE service_areas
  ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'America/Regina';
