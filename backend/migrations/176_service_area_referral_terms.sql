-- Migration 176: per-service-area referral Terms & Conditions (free text)
--
-- Migration 173 made the referral reward NUMBERS configurable per area. The
-- human-readable Terms & Conditions shown on the rider/driver "Refer & Earn"
-- screens, however, were still hardcoded f-strings in the backend
-- (routes/users.py, routes/drivers.py). This migration lets admins write
-- free-text T&C per service area, separately for riders and drivers (their
-- reward structures differ).
--
-- Design:
--   * Two additive, nullable TEXT columns on service_areas. NULL means "no
--     override" → the backend keeps generating the dynamic default sentence
--     from the area's reward numbers (utils/referral_terms.py), so every
--     existing area's behaviour is unchanged until an admin sets text.
--   * Separate rider vs driver columns: rider terms describe give/get; driver
--     terms describe earn-per-qualified-referral.
--
-- All changes are additive + nullable → forward-compatible with in-flight
-- traffic; no backfill required.
--
-- Rollback (on paper — drop the added columns):
--   ALTER TABLE service_areas
--     DROP COLUMN IF EXISTS rider_referral_terms,
--     DROP COLUMN IF EXISTS driver_referral_terms;
--   (Dropping reverts both apps to the auto-generated T&C sentence.)

ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS rider_referral_terms  text;
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS driver_referral_terms text;
