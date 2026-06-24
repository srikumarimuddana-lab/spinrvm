-- Migration 189: referral completion deadline (window_days) + 'expired' payout state
--
-- Until now a referral reward had only a RIDE-COUNT criterion: the referee had
-- to complete N completed rides (driver: 10, rider: 1) — with no time limit, so
-- a driver could take a year to reach the target and the referrer would still
-- be paid. This migration adds a DEADLINE: the referee must reach the ride
-- threshold within `window_days` of referral_applied_at, otherwise the referral
-- expires unpaid. This pushes referred drivers to complete their rides promptly.
--
-- Design:
--   * Two additive columns on service_areas hold each area's deadline, separate
--     for riders and drivers (their thresholds differ). DEFAULT 30 days applies
--     a deadline everywhere immediately (admins override per area from the
--     Service Areas screen; the backend uses the same 30-day global constant for
--     riders/drivers who resolve to NO area — see utils/referral_terms.py).
--     A value of 0 disables the deadline for that area (no time limit).
--   * referral_payouts.status gains 'expired': a terminal, never-paid state the
--     payout loop records when the window lapses before the threshold is met.
--     Recording it (rewards 0) reuses the UNIQUE(referee_user_id) claim so the
--     referee is never re-checked or paid.
--
-- All service_areas changes are additive + defaulted → forward-compatible with
-- in-flight traffic; no backfill of large tables required. The CHECK swap is an
-- ADD to the allowed set (widening), safe against in-flight writes which only
-- ever insert the existing states.
--
-- Rollback (on paper):
--   ALTER TABLE service_areas
--     DROP COLUMN IF EXISTS rider_referral_window_days,
--     DROP COLUMN IF EXISTS driver_referral_window_days;
--   ALTER TABLE referral_payouts DROP CONSTRAINT IF EXISTS referral_payouts_status_check;
--   ALTER TABLE referral_payouts ADD CONSTRAINT referral_payouts_status_check
--     CHECK (status IN ('processing', 'paid', 'failed'));
--   (Any rows already in 'expired' must be reconciled before re-adding the
--    narrower constraint, or the ADD will fail.)

-- ── service_areas: per-area referral completion deadline (days) ──────
-- 30-day default; 0 = no deadline for that area.
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS rider_referral_window_days  integer NOT NULL DEFAULT 30;
ALTER TABLE service_areas
    ADD COLUMN IF NOT EXISTS driver_referral_window_days integer NOT NULL DEFAULT 30;

-- ── referral_payouts: add the terminal 'expired' state ──────────────
ALTER TABLE referral_payouts
    DROP CONSTRAINT IF EXISTS referral_payouts_status_check;
ALTER TABLE referral_payouts
    ADD CONSTRAINT referral_payouts_status_check
    CHECK (status IN ('processing', 'paid', 'failed', 'expired'));
