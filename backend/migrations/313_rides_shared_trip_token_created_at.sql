-- 313_rides_shared_trip_token_created_at.sql
--
-- routes/rides/sharing.py's 24h share-link expiry check (`track_shared_ride`),
-- services/company_booking_service.py's immediate corporate guest booking,
-- and services/guest_notification_service.py all read/write
-- rides.shared_trip_token_created_at — but no migration ever created this
-- column. Confirmed missing on the live production `rides` table
-- (information_schema.columns) alongside the sibling `shared_trip_token`
-- column, which DOES exist (migration history unclear on when/how that one
-- landed without this one — not investigated further, this closes the gap
-- either way).
--
-- Impact: every write that sets both columns together (a rider using
-- "share my trip" for the first time on a ride, and every immediate
-- (non-scheduled) corporate portal guest booking, which stamps this at
-- insert time) fails outright — PostgREST rejects an INSERT/UPDATE payload
-- referencing an unknown column. Reported as "database operation failed"
-- when booking a ride in the corporate portal.
--
-- rollback: ALTER TABLE rides DROP COLUMN IF EXISTS shared_trip_token_created_at;
--   Safe at any time — nothing else depends on this column existing beyond
--   the three call sites above, which already handle a NULL/missing value
--   gracefully (sharing.py's `if token_created:` guard).

ALTER TABLE rides
    ADD COLUMN IF NOT EXISTS shared_trip_token_created_at TIMESTAMPTZ;

COMMENT ON COLUMN rides.shared_trip_token_created_at IS
    'When shared_trip_token was minted — routes/rides/sharing.py expires the '
    'public tracking link 24h after this timestamp. Set at ride-insert time '
    'for immediate corporate guest bookings, or on first "share my trip" for '
    'a self-booked ride.';
