-- 230_rides_booked_by_member.sql
--
-- Book-on-behalf-of-staff (Phase 5 / M5.6, owner Q12): an admin or section
-- head can create a ride FOR a staff member. Attribution needs two axes:
--
--   rides.corporate_member_id (migration 36)   = the PAYING member — for
--     on-behalf bookings that is the TARGET staff member, whose
--     allowance → section → master chain settles the fare.
--   rides.corporate_booked_by_member_id (THIS) = who actually booked it.
--     Distinct from the shipped guest flow (there the booker IS the payer).
--
-- Mirrors migration 36 exactly: nullable UUID FK to corporate_members with
-- ON DELETE SET NULL (attribution survives member removal as NULL, never
-- blocks it), partial index for the "rides I booked" dashboard/list filter.
--
-- Forward-compatible: nullable metadata-only ADD COLUMN. Existing rows read
-- NULL (they were booked before booker attribution existed) and are excluded
-- from the partial index at build time; readers fall back to
-- corporate_member_id. No new table → RLS posture unchanged.
--
-- Rollback (on paper):
--   DROP INDEX IF EXISTS idx_rides_corporate_booked_by;
--   ALTER TABLE rides DROP COLUMN IF EXISTS corporate_booked_by_member_id;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS corporate_booked_by_member_id UUID REFERENCES corporate_members(id) ON DELETE SET NULL;

-- CONCURRENTLY: rides is a HOT table (dispatch/payments read-write it under
-- live traffic) — a blocking index build would stall those paths. The
-- CONCURRENTLY keyword routes this whole file through migrate.py's
-- statement-split autocommit path (no wrapping transaction, as required).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_corporate_booked_by
    ON rides (corporate_booked_by_member_id)
    WHERE corporate_booked_by_member_id IS NOT NULL;
