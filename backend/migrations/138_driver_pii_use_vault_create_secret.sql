-- Migration 138: Use Supabase Vault's supported create_secret() path for driver PII.
-- Rollback: restore encrypt_driver_pii(text) from migration 32/137 and re-apply
-- service_role-only EXECUTE grants. Not recommended unless vault.create_secret()
-- regresses, because direct INSERT into vault.secrets can fail on managed
-- Supabase with permission denied for vault._crypto_aead_det_noncegen().

CREATE OR REPLACE FUNCTION public.encrypt_driver_pii(plaintext text)
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
    'driver_pii'
  );

  RETURN _secret_id::text;
END;
$$;

ALTER FUNCTION public.decrypt_driver_pii(text)
  SET search_path = public, vault, pg_temp;

REVOKE EXECUTE ON FUNCTION public.encrypt_driver_pii(text) FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.decrypt_driver_pii(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.encrypt_driver_pii(text) TO service_role;
GRANT EXECUTE ON FUNCTION public.decrypt_driver_pii(text) TO service_role;

COMMENT ON FUNCTION public.encrypt_driver_pii(text) IS
  'Stores driver PII in Supabase Vault through vault.create_secret(); returns the vault.secrets UUID.';
