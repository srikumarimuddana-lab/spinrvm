-- Migration 357: Encrypt emergency_contacts.name/phone using Supabase Vault
--
-- Ranked blocker #13 / decision-log item #13 (docs/audit/2026-08-19-decision-writeups.md
-- section 1; decided 2026-08-21, sign-off in docs/audit/2026-08-21-emergency-contact-pia-memo.md
-- Section 9): a rider's emergency contact is a third-party PIPEDA data subject whose name/phone
-- were stored as plain TEXT with no encryption. Closes PIA risk R-001 (safeguards).
--
-- Rollback:
--   -- Data written after this migration (vault-secret UUIDs) will read back as
--   -- opaque UUID strings once decrypt_emergency_contact_pii is gone -- this is
--   -- NOT reversible without a data-level remediation (re-encrypt back to plaintext
--   -- via the RPC before dropping it, or accept the post-migration rows are unreadable).
--   -- Rows written BEFORE this migration are untouched plaintext and are unaffected
--   -- by a rollback.
--   DROP FUNCTION IF EXISTS encrypt_emergency_contact_pii(text);
--   DROP FUNCTION IF EXISTS decrypt_emergency_contact_pii(text);
--
-- Encryption model: identical to drivers.license_number/vehicle_vin -- but written
-- directly against the CORRECTED, twice-patched final form of that pattern
-- (migrations 32 -> 78 -> 137 -> 138), not migration 32's original draft. That
-- original draft (raw `INSERT INTO vault.secrets`, no `search_path` pin) shipped
-- two real production bugs for driver PII that later migrations had to fix:
--   - Migration 78 pinned `search_path` after a schema-confusion privilege-
--     escalation review finding on unpinned SECURITY DEFINER PII functions.
--   - Migration 137 then found that pin (public, pg_temp) broke pgsodium's
--     internal crypto resolution -- "permission denied for function
--     _crypto_aead_det_noncegen" (42501) -- and had to widen search_path and
--     transfer function ownership to supabase_admin (which natively holds
--     pgsodium privileges on managed Supabase).
--   - Migration 138 replaced the raw `vault.secrets` INSERT with the
--     officially supported `vault.create_secret()` API, which is the actual
--     currently-running implementation for driver PII.
-- This migration ships that known-good end state directly for
-- emergency_contacts.name/phone, rather than reintroducing and re-fixing the
-- same two bugs against a new table. NO custom pgsodium key is created --
-- migration 138's driver-PII implementation doesn't pass one to
-- vault.create_secret() either (it relies on vault's default key), so a
-- per-domain key here would be unused dead code, not real isolation.
--
-- We do NOT use Supabase Transparent Column Encryption (removed by Supabase
-- in mid-2024). emergency_contacts.name and .phone stay as plain TEXT
-- columns -- NO SCHEMA CHANGE, no new columns, no backfill migration needed
-- -- and the application explicitly calls encrypt_emergency_contact_pii()/
-- decrypt_emergency_contact_pii() via RPC on every write/read going forward.
-- The columns hold vault.secrets UUIDs after this migration, not plaintext;
-- actual ciphertext lives in vault.secrets, encrypted by pgsodium.
--
-- Dual-read, no backfill batch needed: decrypt_emergency_contact_pii() falls
-- back to returning its input verbatim when that input isn't a valid UUID --
-- i.e. an existing plaintext row (written before this migration, and never
-- since re-saved, since there is no UPDATE endpoint for this table -- riders
-- delete and re-add to change a contact) keeps decrypting correctly forever.
-- Only rows written or re-added after this migration ships get encrypted.
--
-- COORDINATED DEPLOY: this migration alone does not close PIA risk R-001 --
-- the application code in backend/routes/users.py must also be changed to
-- call these two RPCs on every emergency-contact write/read (tracked as its
-- own task, landing alongside this migration). Until that app-code change
-- ships, this migration is a no-op: new rows keep landing as plaintext via
-- the existing insert_one/get_rows calls, which don't invoke these
-- functions. See docs/change-log/2026-08-21-emergency-contact-encryption-consent.md.

-- ─────────────────────────────────────────────────────────────────────────────
-- Postgres helper functions used by the application (via Supabase RPC)
-- ─────────────────────────────────────────────────────────────────────────────

-- encrypt_emergency_contact_pii(plaintext) → stores the value in vault.secrets
-- via the supported vault.create_secret() API, returns the secret UUID that
-- the application stores in the column. NULL/empty input passes through
-- unchanged (matches migration 138's fixed encrypt_driver_pii guard -- no
-- point creating a vault secret for an empty value).
CREATE OR REPLACE FUNCTION encrypt_emergency_contact_pii(plaintext text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, vault, pg_temp
AS $$
DECLARE
  _secret_id uuid;
BEGIN
  IF plaintext IS NULL OR plaintext = '' THEN
    RETURN plaintext;
  END IF;

  _secret_id := vault.create_secret(
    plaintext,
    NULL,
    'emergency_contact_pii'
  );

  RETURN _secret_id::text;
END;
$$;

-- decrypt_emergency_contact_pii(secret_id) → returns the plaintext from
-- vault.secrets, or the input verbatim if it isn't a valid UUID (a
-- pre-migration plaintext row -- see the dual-read note above).
CREATE OR REPLACE FUNCTION decrypt_emergency_contact_pii(secret_id text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, vault, pg_temp
AS $$
DECLARE
  _plaintext text;
BEGIN
  SELECT decrypted_secret INTO _plaintext
    FROM vault.decrypted_secrets
   WHERE id = secret_id::uuid
   LIMIT 1;

  RETURN _plaintext;
EXCEPTION
  WHEN invalid_text_representation THEN
    -- secret_id is not a UUID — the column still holds plaintext (pre-migration row)
    RETURN secret_id;
END;
$$;

-- Ownership: transfer to supabase_admin, which natively holds the pgsodium
-- privileges vault.create_secret() needs internally on managed Supabase --
-- matches migration 137's fix, applied here from the start rather than
-- discovered via a production permission-denied error a second time.
ALTER FUNCTION encrypt_emergency_contact_pii(text) OWNER TO supabase_admin;
ALTER FUNCTION decrypt_emergency_contact_pii(text) OWNER TO supabase_admin;

-- Restrict: only the service role may call these functions.
REVOKE EXECUTE ON FUNCTION encrypt_emergency_contact_pii(text) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION decrypt_emergency_contact_pii(text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION encrypt_emergency_contact_pii(text) TO service_role;
GRANT  EXECUTE ON FUNCTION decrypt_emergency_contact_pii(text) TO service_role;

COMMENT ON FUNCTION encrypt_emergency_contact_pii(text) IS
  'Stores an emergency contact''s name/phone in Supabase Vault via vault.create_secret(); returns the vault.secrets UUID. Ranked blocker #13.';
COMMENT ON FUNCTION decrypt_emergency_contact_pii(text) IS
  'Reads an emergency contact''s name/phone back from Supabase Vault, or returns the input verbatim for a pre-migration plaintext row (dual-read).';

-- ─────────────────────────────────────────────────────────────────────────────
-- Column-level SELECT restrictions
--    Stored values are vault.secrets UUIDs after this migration, not
--    plaintext, so a raw column leak wouldn't reveal PII on its own for
--    newly-written rows -- but pre-migration rows (dual-read, see above)
--    still hold plaintext until a rider deletes/re-adds them. The backend
--    already reads this table via the service_role key (RLS/column grants
--    don't apply to service_role), so this REVOKE only hardens against a
--    hypothetical direct-PostgREST read using a rider's own JWT even if RLS
--    were ever misconfigured — defense in depth, matching migration 32's
--    identical rationale for drivers.license_number/vehicle_vin.
-- ─────────────────────────────────────────────────────────────────────────────
REVOKE SELECT (name)  ON TABLE emergency_contacts FROM anon, authenticated;
REVOKE SELECT (phone) ON TABLE emergency_contacts FROM anon, authenticated;
