-- 207_guest_bookings.sql
--
-- Corporate guest booking (Spinr for Business): companies book rides on
-- behalf of customers who may have no Spinr account. The customer is
-- provisioned as a users row keyed by phone (users.is_guest), so
-- rides.rider_id keeps its FK and the account is auto-claimed the first time
-- that phone logs in via OTP (verify_otp find-or-create matches the row and
-- flips is_guest off). rides.guest_booking is FROZEN at booking time and
-- drives SMS-vs-push notifications, server-side auto-settlement, and portal
-- labeling — deriving it later breaks when the customer is themselves a
-- member or installs the app mid-ride.
--
-- Forward-compatible: BOOLEAN ... NOT NULL DEFAULT false adds are
-- metadata-only on PG 11+ (no table rewrite); indexes are additive.
-- guest_booking / is_guest carry no PII.
--
-- New query patterns served (index-with-query-pattern rule):
--   * portal live-bookings list:
--       WHERE corporate_account_id = ? ORDER BY created_at DESC
--     (rides.corporate_account_id has an FK since migration 17 but no index)
--   * guest-row maintenance/reporting: WHERE is_guest
--
-- Rollback (on paper):
--   DROP INDEX IF EXISTS idx_users_is_guest;
--   DROP INDEX IF EXISTS idx_rides_corporate_account_created;
--   ALTER TABLE rides DROP COLUMN IF EXISTS guest_booking;
--   ALTER TABLE users DROP COLUMN IF EXISTS is_guest;

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_guest BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rides ADD COLUMN IF NOT EXISTS guest_booking BOOLEAN NOT NULL DEFAULT FALSE;

-- CONCURRENTLY: rides and users are hot tables (every state transition /
-- login writes them); a plain CREATE INDEX would block writes for the build.
-- The migrate runner detects CONCURRENTLY and applies the file per-statement
-- in autocommit. Post-deploy: verify pg_index.indisvalid for both (a failed
-- concurrent build leaves an INVALID index that IF NOT EXISTS would skip).
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rides_corporate_account_created
    ON rides (corporate_account_id, created_at DESC)
    WHERE corporate_account_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_users_is_guest
    ON users (is_guest) WHERE is_guest;

COMMENT ON COLUMN users.is_guest IS
    'Row created by a corporate guest booking (no OTP signup yet); flipped false on first OTP login (account claim).';
COMMENT ON COLUMN rides.guest_booking IS
    'Frozen at booking: ride was booked by a company for a customer. Drives SMS lifecycle + corporate auto-settlement.';
