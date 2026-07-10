-- 224_corporate_self_serve_signup.sql
--
-- Self-serve company signup (Spinr for Business, Uber-for-Business model):
-- companies can now register themselves on the portal (authenticated with the
-- existing work-email OTP session) instead of being staff-created only. The
-- account lands in the existing `pending_verification` status and goes through
-- the staff KYB queue unchanged.
--
-- Adds to corporate_accounts:
--   * industry                 — free-text industry label (signup form)
--   * address_line1/2, city, province, postal_code — business address (the
--     province doubles as tax_region source; validated app-side against the
--     Canadian province list)
--   * signup_user_id           — the portal user who self-registered the
--     company. TEXT with NO FK: users.id is TEXT while corporate tables are
--     UUID (pre-existing type drift, see migrations 27/206/213), and users may
--     be hard-deleted at the 7y retention ceiling — the attribution is
--     historical, not relational.
--   * signup_source            — 'staff' | 'self_serve' (existing rows default
--     'staff', which is historically accurate)
--   * terms_accepted_version/at — PIPEDA/business-terms consent recording for
--     self-serve signups (mirrors the users-table consent pattern)
--
-- Partial index: the signup endpoint caps each user at 3 pending_verification
-- companies (abuse guard) — that lookup is `WHERE signup_user_id = ? AND
-- status = 'pending_verification'`, so index exactly that slice.
--
-- Forward-compatible: metadata-only nullable ADD COLUMNs (no table rewrite),
-- guarded CHECK constraint, partial index — safe against in-flight traffic.
-- No new table → RLS posture unchanged (corporate_accounts has RLS enabled
-- with the admin-only baseline policy from migration 17; portal access goes
-- through the service-role backend behind require_company_* guards).
--
-- Rollback (on paper):
--   DROP INDEX IF EXISTS corp_accounts_signup_pending_idx;
--   ALTER TABLE corporate_accounts DROP CONSTRAINT IF EXISTS corporate_accounts_signup_source_check;
--   ALTER TABLE corporate_accounts
--     DROP COLUMN IF EXISTS industry,
--     DROP COLUMN IF EXISTS address_line1,
--     DROP COLUMN IF EXISTS address_line2,
--     DROP COLUMN IF EXISTS city,
--     DROP COLUMN IF EXISTS province,
--     DROP COLUMN IF EXISTS postal_code,
--     DROP COLUMN IF EXISTS signup_user_id,
--     DROP COLUMN IF EXISTS signup_source,
--     DROP COLUMN IF EXISTS terms_accepted_version,
--     DROP COLUMN IF EXISTS terms_accepted_at;

ALTER TABLE corporate_accounts
    ADD COLUMN IF NOT EXISTS industry               TEXT,
    ADD COLUMN IF NOT EXISTS address_line1          TEXT,
    ADD COLUMN IF NOT EXISTS address_line2          TEXT,
    ADD COLUMN IF NOT EXISTS city                   TEXT,
    ADD COLUMN IF NOT EXISTS province               TEXT,
    ADD COLUMN IF NOT EXISTS postal_code            TEXT,
    ADD COLUMN IF NOT EXISTS signup_user_id         TEXT,
    ADD COLUMN IF NOT EXISTS signup_source          TEXT DEFAULT 'staff',
    ADD COLUMN IF NOT EXISTS terms_accepted_version TEXT,
    ADD COLUMN IF NOT EXISTS terms_accepted_at      TIMESTAMPTZ;

-- Guarded CHECK (same idempotency pattern as migration 27's status/size_tier).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'corporate_accounts_signup_source_check'
    ) THEN
        ALTER TABLE corporate_accounts ADD CONSTRAINT corporate_accounts_signup_source_check
            CHECK (signup_source IN ('staff', 'self_serve'));
    END IF;
END $$;

-- Pending-cap lookup: count a user's pending_verification signups.
CREATE INDEX IF NOT EXISTS corp_accounts_signup_pending_idx
    ON corporate_accounts (signup_user_id)
    WHERE status = 'pending_verification' AND signup_user_id IS NOT NULL;
