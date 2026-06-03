-- 127_zoho_desk_mirror_backfilled.sql
--
-- Rollback:
--   ALTER TABLE zoho_desk_config DROP COLUMN IF EXISTS mirror_backfilled;
--
-- Purpose: completeness marker for the Zoho Desk mirror. The Help Desk reads
-- from the local mirror only AFTER a full backfill has completed; until then it
-- serves live from Zoho so partially-seeded accounts never hide historical
-- tickets or report wrong totals. Set true by the sync once it pages to the end.
--
-- Additive column on the backend-only config table; safe in flight.

ALTER TABLE zoho_desk_config ADD COLUMN IF NOT EXISTS mirror_backfilled BOOLEAN NOT NULL DEFAULT FALSE;

NOTIFY pgrst, 'reload schema';
