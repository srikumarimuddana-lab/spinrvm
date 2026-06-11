-- 143_ride_offers_cancelled_status.sql
-- Adds 'cancelled' to the ride_offers status check constraint.
-- When a rider cancels a ride that is in batch dispatch, rides.py marks any
-- pending offers as 'cancelled' so drivers are notified and freed. The
-- constraint introduced in migration 100 (and widened by 131) did not include
-- this value, causing a 23514 check-constraint violation on every such cancel.
--
-- Forward-compatible: widens the allowed set only. Old backend code that never
-- writes 'cancelled' still satisfies the new constraint.
--
-- Rollback:
--   1. UPDATE ride_offers SET status = 'expired' WHERE status = 'cancelled';
--   2. ALTER TABLE ride_offers DROP CONSTRAINT ride_offers_status_check;
--      ALTER TABLE ride_offers ADD CONSTRAINT ride_offers_status_check
--        CHECK (status IN ('pending','accepted','declined','expired','preempted'));

ALTER TABLE ride_offers DROP CONSTRAINT IF EXISTS ride_offers_status_check;

ALTER TABLE ride_offers
    ADD CONSTRAINT ride_offers_status_check
    CHECK (status IN ('pending', 'accepted', 'declined', 'expired', 'preempted', 'cancelled'));
