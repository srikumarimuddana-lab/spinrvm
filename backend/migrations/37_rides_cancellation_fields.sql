-- Migration 37: rides cancellation audit columns
--
-- Admin "force cancel" from the live-monitoring page writes three fields
-- on the rides row — cancelled_at, cancellation_reason, cancelled_by_admin_id
-- — that had no corresponding columns. Supabase rejected the update with
-- "column does not exist"; run_sync() surfaced it as DatabaseError → 503
-- at /api/admin/rides/{id}/cancel, which is why the cancel button never
-- worked. Add the columns so the write succeeds.
--
-- `cancelled_by_admin_id` is TEXT (not UUID) because the super-admin uses
-- the sentinel id "admin-001" (env-var creds, no admin_staff row) and
-- staff ids are TEXT in admin_staff.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancellation_reason TEXT;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancelled_by_admin_id TEXT;

CREATE INDEX IF NOT EXISTS idx_rides_cancelled_at
    ON rides (cancelled_at)
    WHERE cancelled_at IS NOT NULL;
