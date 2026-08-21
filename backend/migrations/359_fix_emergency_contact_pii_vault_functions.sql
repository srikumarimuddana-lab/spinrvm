-- Migration 359: Fix encrypt_emergency_contact_pii()/decrypt_emergency_contact_pii()
-- to use the corrected vault.create_secret() pattern
--
-- CONTEXT: migration 357 was independently authored and merged twice, by two
-- concurrent sessions, within minutes of each other on 2026-08-21:
--   - This branch's own first draft (never shipped as 357 -- caught before
--     merge by spinr-migration-reviewer, see docs/audit/2026-08-21-
--     emergency-contact-pia-memo.md Section 9's "Parallel work note").
--   - PR #4322 ("feat(safety,privacy): encrypt emergency-contact PII,
--     resolve 3 decision-log items"), merged to main FIRST, and so is the
--     "357" that actually exists in this repo's history.
--
-- PR #4322's version reproduces the exact bug migrations 137/138 already
-- fixed once for driver PII (backend/migrations/32_encrypt_sensitive_fields.sql
-- -> 78 -> 137 -> 138): it INSERTs directly into vault.secrets(secret, key_id)
-- rather than calling the supported vault.create_secret() API. On managed
-- Supabase this raises "permission denied for function
-- _crypto_aead_det_noncegen" (42501) -- the SECURITY DEFINER function owner
-- lacks the internal pgsodium privileges vault.create_secret() needs, which
-- only vault.create_secret() (not a raw INSERT) resolves correctly, and only
-- when the function OWNER is supabase_admin.
--
-- IMPACT SO FAR: low. The app-code call path
-- (encrypt_emergency_contact_pii RPC) fails closed on any RPC error
-- (backend/routes/users.py's _encrypt_emergency_contact_pii, and PR #4322's
-- own equivalent) -- so if this bug has already been hit in production, it
-- would have surfaced as 503s on POST /emergency-contacts, not silent
-- plaintext writes or corrupted data. No backfill or data remediation is
-- needed here; this is a pure function-body fix.
--
-- FIX: CREATE OR REPLACE both functions in place (same names, same
-- signatures -- no app-code call site needs to change) with the corrected
-- pattern already proven in production for driver PII (migration 138's end
-- state): vault.create_secret(), search_path pinned to
-- (public, vault, pg_temp), OWNER TO supabase_admin. This migration is
-- idempotent and safe to run even if 357's buggy functions were never
-- successfully invoked (CREATE OR REPLACE FUNCTION has no data-dependent
-- side effects of its own).
--
-- We deliberately do NOT touch migration 357 itself (append-only rule,
-- backend/migrations/CLAUDE.md) -- this is a forward-fixing migration, not
-- an edit.
--
-- Rollback: re-run 357's original CREATE OR REPLACE bodies to restore the
--   pre-359 (buggy) function definitions -- not recommended, since that
--   reintroduces the 42501 error. There is no data to roll back: this
--   migration only replaces function bodies.

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
-- privileges vault.create_secret() needs internally on managed Supabase.
-- Migration 357 (PR #4322's version) did not set this — a required part of
-- the fix, not optional hardening.
ALTER FUNCTION encrypt_emergency_contact_pii(text) OWNER TO supabase_admin;
ALTER FUNCTION decrypt_emergency_contact_pii(text) OWNER TO supabase_admin;

-- Grants: re-affirm (idempotent — 357 already set these identically for
-- both functions, unaffected by CREATE OR REPLACE, but restating makes this
-- migration correct standalone if ever read out of order).
REVOKE EXECUTE ON FUNCTION encrypt_emergency_contact_pii(text) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION decrypt_emergency_contact_pii(text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION encrypt_emergency_contact_pii(text) TO service_role;
GRANT  EXECUTE ON FUNCTION decrypt_emergency_contact_pii(text) TO service_role;

COMMENT ON FUNCTION encrypt_emergency_contact_pii(text) IS
  'Stores an emergency contact''s name/phone in Supabase Vault via vault.create_secret(); returns the vault.secrets UUID. Fixed in migration 359 (see top comment) — do not revert to a raw vault.secrets INSERT.';
COMMENT ON FUNCTION decrypt_emergency_contact_pii(text) IS
  'Reads an emergency contact''s name/phone back from Supabase Vault, or returns the input verbatim for a pre-migration plaintext row (dual-read). Fixed in migration 359 — do not revert to unpinned search_path or non-supabase_admin ownership.';
