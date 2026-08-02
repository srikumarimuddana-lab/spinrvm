-- 279_safety_incidents_merge.sql
-- Corporate + admin portal review, round 2: "safety incidents can't be
-- created or merged from the admin side" — routes/admin/safety.py only
-- had list/get/PATCH-status. Two duplicate SOS reports for the same
-- incident (e.g. both rider and driver report the same event) had no way
-- to be linked together short of manually editing resolution_notes.
--
-- The table's own CHECK constraint already anticipated a 'duplicate'
-- status; this migration adds the column to record *which* incident a
-- duplicate points to, so "find every report merged into incident X" is
-- a real query instead of a free-text search over resolution_notes.
-- No FK constraint -- this table's other cross-reference column (ride_id)
-- doesn't use one either (see 94_safety_incidents.sql), same convention.
--
-- Additive, nullable: existing rows get NULL (never merged).
--
-- Rollback:
--   DROP INDEX IF EXISTS idx_safety_incidents_merged_into;
--   ALTER TABLE safety_incidents DROP COLUMN IF EXISTS merged_into_incident_id;

ALTER TABLE safety_incidents
    ADD COLUMN IF NOT EXISTS merged_into_incident_id TEXT DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_safety_incidents_merged_into
    ON safety_incidents (merged_into_incident_id)
    WHERE merged_into_incident_id IS NOT NULL;
