-- rollback:
--   ALTER TABLE admin_staff
--     DROP COLUMN IF EXISTS mfa_enabled,
--     DROP COLUMN IF EXISTS mfa_secret,
--     DROP COLUMN IF EXISTS mfa_secret_pending,
--     DROP COLUMN IF EXISTS mfa_backup_codes;

ALTER TABLE admin_staff
  ADD COLUMN IF NOT EXISTS mfa_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS mfa_secret         TEXT,
  ADD COLUMN IF NOT EXISTS mfa_secret_pending TEXT,
  ADD COLUMN IF NOT EXISTS mfa_backup_codes   JSONB;

COMMENT ON COLUMN admin_staff.mfa_secret         IS 'Base-32 TOTP secret (active). Stored in plaintext; protect via Supabase row-level security and service-role-only access.';
COMMENT ON COLUMN admin_staff.mfa_secret_pending IS 'Unconfirmed TOTP secret during enrollment. Cleared on confirm or re-enroll.';
COMMENT ON COLUMN admin_staff.mfa_backup_codes   IS 'Array of {hash, used} objects. Hashes are SHA-256 of the plaintext code.';
