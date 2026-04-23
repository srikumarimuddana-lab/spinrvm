-- Migration 38: rides cancellation attribution
--
-- "Who cancelled this ride?" was answerable only by heuristics:
--   • admin cancels → cancelled_by_admin_id is set (migration 37)
--   • driver cancels → cancelled_by="driver" (written by routes/drivers.py
--                      but the column wasn't in the schema, so writes hit
--                      an except-branch and were silently dropped)
--   • auto-cancel after search timeout → status=cancelled, reason string
--     "No nearby drivers found. Please try again." (free-text match)
--   • rider cancels → nothing distinguishes it
--
-- Add two explicit columns so the admin panel can filter on them and
-- reports can aggregate without parsing reason strings.
--
-- cancelled_by: who initiated the cancel — 'rider' | 'driver' | 'admin' | 'system'
-- cancellation_type: finer-grained category, in particular 'no_drivers_found'
--   vs 'rider_cancel' / 'driver_cancel' / 'admin_cancel' / 'timeout'. The
--   column is free-text TEXT (not an enum) so we can add new categories
--   without another migration.

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancelled_by TEXT;

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS cancellation_type TEXT;

CREATE INDEX IF NOT EXISTS idx_rides_cancellation_type
    ON rides (cancellation_type)
    WHERE cancellation_type IS NOT NULL;

COMMENT ON COLUMN rides.cancelled_by IS
    'Initiator of cancellation: rider | driver | admin | system';
COMMENT ON COLUMN rides.cancellation_type IS
    'Category: rider_cancel | driver_cancel | admin_cancel | no_drivers_found | timeout';
