-- 131_ride_offers_preempted_status.sql
-- Adds a 'preempted' status to ride_offers. In batch dispatch, when one driver
-- accepts, the other drivers' still-pending offers were marked 'expired' — the
-- same value a real timeout uses — so the Driver Offers analytics counted
-- preempted drivers as having "ignored" the offer. 'preempted' lets us separate
-- "another driver accepted first" from a genuine timeout.
--
-- Forward-compatible: widens the allowed set only (no existing row violates the
-- new constraint); old backend code that only writes the original four values
-- still satisfies it. accept_ride now writes 'preempted' for losing offers.
--
-- Rollback:
--   1. UPDATE ride_offers SET status = 'expired' WHERE status = 'preempted';
--   2. ALTER TABLE ride_offers DROP CONSTRAINT ride_offers_status_check;
--      ALTER TABLE ride_offers ADD CONSTRAINT ride_offers_status_check
--        CHECK (status IN ('pending','accepted','declined','expired'));

ALTER TABLE ride_offers DROP CONSTRAINT IF EXISTS ride_offers_status_check;

ALTER TABLE ride_offers
    ADD CONSTRAINT ride_offers_status_check
    CHECK (status IN ('pending', 'accepted', 'declined', 'expired', 'preempted'));
