-- Migration 413: purge_driver_pii_secret() — delete a driver-PII vault secret
--
-- CONTEXT: migration 289's own top comment already documents this gap —
-- "Dropping `sin` orphans rows in `vault.secrets` — it does NOT delete the
-- ciphertext. A rollback intended to erase collected SINs must also remove
-- those secrets ... Run that BEFORE dropping the column, or the ids are
-- lost and the ciphertext is unreachable but retained — which is a PIPEDA
-- problem, not a clean rollback." No RPC existed to do that deletion from
-- application code — encrypt_driver_pii()/decrypt_driver_pii() (migration
-- 32 -> 78 -> 137 -> 138) only write and read; nothing deletes.
--
-- This closes that gap ahead of ACTION_ITEMS.md A34's dormant-driver SIN
-- purge tool (docs/change-log/2026-09-11-a34-dormant-driver-sin-purge-tool.md),
-- which needs to delete the actual vault.secrets ciphertext, not just null
-- the column reference on `drivers` (nulling the column alone would leave
-- the SIN retained, unreachable-but-present — the exact PIPEDA problem
-- migration 289 already warned about).
--
-- Generic across every driver-PII field that goes through encrypt_driver_pii
-- (sin, license_number, vehicle_vin) — matching that function's own scope,
-- not narrowed to sin specifically. Deletes the vault.secrets row by its id;
-- returns whether a row was actually deleted (false if the id was already
-- gone, e.g. re-run after a partial apply).
--
-- Pattern mirrors decrypt_driver_pii's own SECURITY DEFINER / search_path /
-- ownership (migrations 137/138), since a DELETE against vault.secrets
-- needs the same supabase_admin-held pgsodium privileges as the encrypt
-- path's vault.create_secret() call (see migration 359's fix for the same
-- class of permission issue on a different table's PII functions) — this
-- migration ships pinned/owned correctly from the start rather than needing
-- a follow-up fix like 137/138/359 all were for their own function.
--
-- Rollback: DROP FUNCTION IF EXISTS public.purge_driver_pii_secret(text);
-- No data to roll back — this migration only adds a function.

CREATE OR REPLACE FUNCTION public.purge_driver_pii_secret(secret_id text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, vault, pg_temp
AS $$
DECLARE
  _deleted_count integer;
BEGIN
  IF secret_id IS NULL OR secret_id = '' THEN
    RETURN false;
  END IF;

  DELETE FROM vault.secrets WHERE id = secret_id::uuid;
  GET DIAGNOSTICS _deleted_count = ROW_COUNT;

  RETURN _deleted_count > 0;
EXCEPTION
  WHEN invalid_text_representation THEN
    -- secret_id is not a UUID -- the column held plaintext (pre-encryption
    -- row) or was already null; nothing in vault.secrets to delete.
    RETURN false;
END;
$$;

ALTER FUNCTION public.purge_driver_pii_secret(text) OWNER TO supabase_admin;

REVOKE EXECUTE ON FUNCTION public.purge_driver_pii_secret(text) FROM PUBLIC, anon, authenticated;
GRANT  EXECUTE ON FUNCTION public.purge_driver_pii_secret(text) TO service_role;

COMMENT ON FUNCTION public.purge_driver_pii_secret(text) IS
  'Deletes a driver-PII vault.secrets row (sin/license_number/vehicle_vin ciphertext) by its id. Call BEFORE nulling the referencing column, or the secret_id is lost and the ciphertext becomes unreachable-but-retained. Returns whether a row was actually deleted.';
