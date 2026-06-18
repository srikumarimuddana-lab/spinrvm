-- Migration 172: referral columns on users
--
-- The rider AND driver referral "apply" paths write referral_code_used and
-- referred_by to users, and the rider path also reads an optional custom
-- referral_code. These columns were used by the existing driver referral code
-- but never had a migration, so a database built purely from migrations is
-- missing them and /users/referral/apply (and the driver equivalent) fail with
-- a missing-column error. Add them idempotently.
--
-- referral_applied_at records WHEN the code was applied so the payout loop can
-- count only rides completed AFTER that point — blocking retroactive rewards
-- (a user who already passed the ride threshold can't apply a code later and be
-- paid for past rides).
--
-- All additive + nullable → forward-compatible with in-flight traffic.
--
-- Rollback:
--   ALTER TABLE users
--     DROP COLUMN IF EXISTS referral_code_used,
--     DROP COLUMN IF EXISTS referred_by,
--     DROP COLUMN IF EXISTS referral_code,
--     DROP COLUMN IF EXISTS referral_applied_at;

ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code_used   TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by          TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code        TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_applied_at  TIMESTAMPTZ;

-- Referral summaries and the payout loop group/filter by these.
CREATE INDEX IF NOT EXISTS idx_users_referred_by        ON users (referred_by);
CREATE INDEX IF NOT EXISTS idx_users_referral_code_used ON users (referral_code_used);
