-- Add email verification tracking to users table.
-- Corporate join-domain was trusting unverified email addresses for
-- domain-based auto-enrollment (finding 15 / WS-11).
--
-- rollback: ALTER TABLE users DROP COLUMN IF EXISTS email_verified;
--           ALTER TABLE users DROP COLUMN IF EXISTS email_verified_at;

ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;

-- Index for queries that filter on verification status (admin audit).
CREATE INDEX IF NOT EXISTS idx_users_email_verified ON users (email_verified) WHERE email_verified = false;
