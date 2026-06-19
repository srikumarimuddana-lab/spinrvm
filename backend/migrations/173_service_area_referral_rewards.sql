-- Migration 173: per-service-area referral reward configuration
--
-- Until now the rider and driver referral rewards were global hardcoded
-- constants in the backend (routes/users.py: $5/$5, 1 ride; routes/drivers.py:
-- $10, 10 rides). This migration makes them configurable PER SERVICE AREA so
-- admins can tune referral economics by market from the Service Areas screen.
--
-- Design:
--   * Five additive columns on service_areas hold each area's terms. Defaults
--     equal the current global constants, so every existing area keeps today's
--     behaviour until an admin changes it. The backend still falls back to the
--     global constants when a rider/driver resolves to NO area (see
--     utils/referral_terms.py).
--   * referral_payouts gains service_area_id: the area whose terms were applied,
--     snapshotted at claim time. Stored as plain TEXT (no FK) on purpose — it is
--     a historical financial record and must survive a later service-area
--     deletion/rename, exactly like the numeric reward columns already do. NULL
--     means the global-default fallback was used.
--
-- Money columns are numeric (never float), matching referral_payouts.
-- All changes are additive + nullable/defaulted → forward-compatible with
-- in-flight traffic; no backfill of large tables required.
--
-- Rollback:
--   (on paper — drop the added columns)
--   ALTER TABLE service_areas
--     DROP COLUMN IF EXISTS rider_referrer_reward,
--     DROP COLUMN IF EXISTS rider_referee_reward,
--     DROP COLUMN IF EXISTS rider_referral_rides_required,
--     DROP COLUMN IF EXISTS driver_referral_reward,
--     DROP COLUMN IF EXISTS driver_referral_rides_required;
--   ALTER TABLE referral_payouts DROP COLUMN IF EXISTS service_area_id;
--   (Dropping the columns reverts the backend to the global constants; rewards
--    already paid are unaffected.)

-- ── service_areas: per-area referral terms ──────────────────────────
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS rider_referrer_reward          numeric(10, 2) NOT NULL DEFAULT 5;
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS rider_referee_reward           numeric(10, 2) NOT NULL DEFAULT 5;
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS rider_referral_rides_required  integer        NOT NULL DEFAULT 1;
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS driver_referral_reward         numeric(10, 2) NOT NULL DEFAULT 10;
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS driver_referral_rides_required integer        NOT NULL DEFAULT 10;

-- ── referral_payouts: lock the paying area at claim time ────────────
-- Plain text snapshot (no FK) so the ledger row records which area's terms were
-- applied even if that area is later deleted. NULL = global-default fallback.
ALTER TABLE referral_payouts
    ADD COLUMN IF NOT EXISTS service_area_id text;

-- Supports per-area referral payout reporting (admin analytics filter by area).
CREATE INDEX IF NOT EXISTS idx_referral_payouts_service_area
    ON referral_payouts (service_area_id);
