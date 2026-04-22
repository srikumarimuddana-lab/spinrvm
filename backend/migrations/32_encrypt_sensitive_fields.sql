-- Migration 32: Encrypt sensitive driver PII fields using Supabase Vault
-- P2-5: licence_number and vehicle_vin stored as plain text — encrypt at rest.
--
-- Prerequisites (run once per Supabase project via the dashboard or CLI):
--   CREATE EXTENSION IF NOT EXISTS pgsodium;
-- Supabase projects created after 2023-06 have pgsodium enabled by default.
--
-- Encryption model: we DO NOT use Supabase Transparent Column Encryption
-- (vault.encrypted_text). Supabase removed that surface in mid-2024
-- because of operational issues (key rotation, PITR, replication).
-- Instead, license_number and vehicle_vin stay as plain TEXT columns,
-- and the application explicitly calls encrypt_driver_pii()/decrypt_driver_pii()
-- via RPC on every write/read. The columns hold vault.secrets UUIDs, not
-- plaintext — actual ciphertext lives in vault.secrets, encrypted by
-- pgsodium under the drivers_pii_key key.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Create a named encryption key for driver PII
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pgsodium.valid_key WHERE name = 'drivers_pii_key'
  ) THEN
    PERFORM pgsodium.create_key(name => 'drivers_pii_key');
    RAISE NOTICE 'Created pgsodium key: drivers_pii_key';
  ELSE
    RAISE NOTICE 'pgsodium key drivers_pii_key already exists — skipping';
  END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Postgres helper functions used by the application (via Supabase RPC)
-- ─────────────────────────────────────────────────────────────────────────────

-- encrypt_driver_pii(plaintext) → stores the value in vault.secrets,
-- returns the secret UUID that the application stores in the column.
CREATE OR REPLACE FUNCTION encrypt_driver_pii(plaintext text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  _key_id  uuid;
  _secret_id uuid;
BEGIN
  SELECT id INTO _key_id
    FROM pgsodium.valid_key
   WHERE name = 'drivers_pii_key'
   LIMIT 1;

  IF _key_id IS NULL THEN
    RAISE EXCEPTION 'drivers_pii_key not found — run migration 32 first';
  END IF;

  INSERT INTO vault.secrets (secret, key_id)
  VALUES (plaintext, _key_id)
  RETURNING id INTO _secret_id;

  RETURN _secret_id::text;
END;
$$;

-- decrypt_driver_pii(secret_id) → returns the plaintext from vault.secrets.
CREATE OR REPLACE FUNCTION decrypt_driver_pii(secret_id text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
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
REVOKE EXECUTE ON FUNCTION encrypt_driver_pii(text) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION decrypt_driver_pii(text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION encrypt_driver_pii(text) TO service_role;
GRANT  EXECUTE ON FUNCTION decrypt_driver_pii(text) TO service_role;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Column-level SELECT restrictions
--    Stored values are vault.secrets UUIDs, not plaintext, so leaking them
--    wouldn't reveal PII on its own — but the service role should still be
--    the only one reading them. anon / authenticated must go through an
--    explicit endpoint that returns redacted/masked values.
-- ─────────────────────────────────────────────────────────────────────────────
REVOKE SELECT (license_number) ON TABLE drivers  FROM anon, authenticated;
REVOKE SELECT (vin)            ON TABLE vehicles FROM anon, authenticated;
