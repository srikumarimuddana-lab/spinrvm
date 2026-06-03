-- 124_zoho_desk_sync_cursor.sql
--
-- Rollback:
--   ALTER TABLE zoho_desk_config DROP COLUMN IF EXISTS sync_cursor;
--
-- Purpose: high-water mark for the incremental Zoho Desk sync. The sync pulls
-- tickets sorted by -modifiedTime and stops once it reaches a ticket modified
-- at or before this cursor, so changes to ANY ticket (including old ones whose
-- status changed) are captured cheaply. `last_synced_at` remains the run
-- timestamp used for the "synced N min ago" UI indicator.
--
-- Additive column on the backend-only config table; safe against live traffic.

ALTER TABLE zoho_desk_config ADD COLUMN IF NOT EXISTS sync_cursor TIMESTAMPTZ;

NOTIFY pgrst, 'reload schema';
