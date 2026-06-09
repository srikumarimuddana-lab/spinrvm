-- Migration 137: Fix pgsodium permission denied on encrypt_driver_pii
-- Rollback: ALTER FUNCTION encrypt_driver_pii(text) SET search_path = public, pg_temp;
--           ALTER FUNCTION decrypt_driver_pii(text) SET search_path = public, pg_temp;
--
-- Error: "permission denied for function _crypto_aead_det_noncegen" (42501)
-- when saving vehicle VIN via PUT /api/v1/drivers/me.
--
-- Root cause: Migration 78 pinned search_path to public,pg_temp for CVE
-- mitigation, but vault.secrets INSERT triggers pgsodium's internal
-- encryption which needs the pgsodium schema resolvable. Additionally,
-- the function owner role may lack EXECUTE on pgsodium internal functions.

-- 1. Expand search_path to include vault and pgsodium schemas
ALTER FUNCTION encrypt_driver_pii(text)  SET search_path = public, vault, pgsodium, pg_temp;
ALTER FUNCTION decrypt_driver_pii(text)  SET search_path = public, vault, pgsodium, pg_temp;

-- 2. Grant EXECUTE on pgsodium crypto functions to the roles that may own
--    the SECURITY DEFINER functions. On managed Supabase the function owner
--    is typically supabase_admin; on self-hosted it may be postgres.
DO $$
BEGIN
  -- Grant to postgres (superuser on self-hosted — no-op if already has it)
  BEGIN
    GRANT USAGE ON SCHEMA pgsodium TO postgres;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pgsodium TO postgres;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Could not grant pgsodium to postgres: %', SQLERRM;
  END;

  -- Grant to supabase_admin (managed Supabase function owner)
  BEGIN
    GRANT USAGE ON SCHEMA pgsodium TO supabase_admin;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pgsodium TO supabase_admin;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Could not grant pgsodium to supabase_admin: %', SQLERRM;
  END;

  -- Grant to service_role (the role the backend connects with)
  BEGIN
    GRANT USAGE ON SCHEMA pgsodium TO service_role;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA pgsodium TO service_role;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Could not grant pgsodium to service_role: %', SQLERRM;
  END;

  -- Ensure vault schema is also accessible
  BEGIN
    GRANT USAGE ON SCHEMA vault TO service_role;
    GRANT ALL ON ALL TABLES IN SCHEMA vault TO service_role;
  EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'Could not grant vault to service_role: %', SQLERRM;
  END;
END $$;
