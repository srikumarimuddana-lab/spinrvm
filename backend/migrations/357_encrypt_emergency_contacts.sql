-- Migration 357: Encrypt emergency_contacts.name / .phone using Supabase Vault
--
-- Resolves ranked blocker #13 (docs/audit/2026-08-19-decision-writeups.md;
-- PIA at docs/audit/2026-08-21-emergency-contact-pia-memo.md, Recommendation 1
-- [HIGH]) — Privacy/Legal approved 2026-08-21. `name`/`phone` are a third
-- party's personal information (the rider's emergency contact, who never
-- consented to being in Spinr's system) and were plaintext at rest, readable
-- by anyone with DB/backup/service_role access.
--
-- Mirrors migration 32's proven pattern for drivers.license_number/vehicle_vin
-- exactly (same Vault/pgsodium approach, same SECURITY DEFINER RPC shape),
-- with a dedicated key so an `emergency_contacts` key compromise or future
-- rotation is isolated from the unrelated `drivers_pii_key`.
--
-- Encryption model: columns stay plain TEXT. The application calls
-- encrypt_emergency_contact_pii()/decrypt_emergency_contact_pii() via RPC on
-- every write/read; the column holds the vault.secrets UUID, not plaintext.
--
-- Unlike migration 32's functions, these pin `SET search_path = public,
-- pg_catalog` on both SECURITY DEFINER functions (root CLAUDE.md's
-- search_path-pinning rule for SECURITY DEFINER functions) — every object
-- reference inside is already schema-qualified (pgsodium.valid_key,
-- vault.secrets, vault.decrypted_secrets) so this is defense-in-depth
-- against a future CREATE OR REPLACE adding an unqualified reference, not a
-- fix to a live exploit. 32's own functions are unaffected (append-only).
--
-- No backfill: matching migration 32's own precedent, existing plaintext rows
-- are left as-is — decrypt_emergency_contact_pii() falls back to returning
-- the stored value unchanged when it isn't a UUID (still-plaintext pre-
-- migration row), so old rows keep working read-side. There is no UPDATE
-- endpoint for a contact (rider deletes and re-adds), so every row written
-- from this point forward is encrypted; a pre-existing contact only becomes
-- encrypted once the rider deletes and re-adds it. This mirrors the accepted
-- residual risk already carried for driver PII since migration 32.
--
-- Rollback: DROP FUNCTION encrypt_emergency_contact_pii(text),
--   decrypt_emergency_contact_pii(text); the columns already hold plain TEXT
--   (either vault UUIDs or pre-migration plaintext) so no column change to
--   revert. A rollback leaves any post-migration rows' name/phone unreadable
--   as vault UUIDs until a forward-fix re-adds the functions — acceptable
--   since this is a pure serialization-layer change, not a data-loss risk
--   (the ciphertext remains intact in vault.secrets either way).

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Create a named encryption key for emergency-contact PII
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pgsodium.valid_key WHERE name = 'emergency_contacts_pii_key'
  ) THEN
    PERFORM pgsodium.create_key(name => 'emergency_contacts_pii_key');
    RAISE NOTICE 'Created pgsodium key: emergency_contacts_pii_key';
  ELSE
    RAISE NOTICE 'pgsodium key emergency_contacts_pii_key already exists — skipping';
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Postgres helper functions used by the application (via Supabase RPC)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION encrypt_emergency_contact_pii(plaintext text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
  _key_id  uuid;
  _secret_id uuid;
BEGIN
  SELECT id INTO _key_id
    FROM pgsodium.valid_key
   WHERE name = 'emergency_contacts_pii_key'
   LIMIT 1;

  IF _key_id IS NULL THEN
    RAISE EXCEPTION 'emergency_contacts_pii_key not found — run migration 357 first';
  END IF;

  INSERT INTO vault.secrets (secret, key_id)
  VALUES (plaintext, _key_id)
  RETURNING id INTO _secret_id;

  RETURN _secret_id::text;
END;
$$;

CREATE OR REPLACE FUNCTION decrypt_emergency_contact_pii(secret_id text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
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

-- Restrict: only the service role may call these functions.
REVOKE EXECUTE ON FUNCTION encrypt_emergency_contact_pii(text) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION decrypt_emergency_contact_pii(text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION encrypt_emergency_contact_pii(text) TO service_role;
GRANT  EXECUTE ON FUNCTION decrypt_emergency_contact_pii(text) TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Column-level SELECT restrictions
--    Stored values are vault.secrets UUIDs going forward, not plaintext, so
--    leaking them wouldn't reveal PII on its own — but the service role
--    should still be the only one reading them directly. RLS already scopes
--    row-level access to the owning rider; this is a defense-in-depth
--    column-level restriction matching migration 32's precedent for drivers.
-- ─────────────────────────────────────────────────────────────────────────────
REVOKE SELECT (name, phone) ON TABLE emergency_contacts FROM anon, authenticated;

NOTIFY pgrst, 'reload schema';
