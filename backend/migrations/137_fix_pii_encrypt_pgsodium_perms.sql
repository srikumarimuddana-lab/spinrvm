-- Migration 137: Fix pgsodium permission denied on encrypt_driver_pii
-- Rollback: ALTER FUNCTION encrypt_driver_pii(text) OWNER TO postgres;
--           ALTER FUNCTION decrypt_driver_pii(text) OWNER TO postgres;
--           ALTER FUNCTION encrypt_driver_pii(text) SET search_path = public, pg_temp;
--           ALTER FUNCTION decrypt_driver_pii(text) SET search_path = public, pg_temp;
--
-- Error: "permission denied for function _crypto_aead_det_noncegen" (42501)
-- when saving vehicle VIN via PUT /api/v1/drivers/me.
--
-- Root cause: Migration 78 pinned search_path to public,pg_temp for CVE
-- mitigation, but vault.secrets INSERT triggers pgsodium's internal
-- encryption which needs the pgsodium schema resolvable. The broad
-- GRANT approach fails on managed Supabase (randombytes_random is
-- locked down). Instead, transfer ownership to supabase_admin which
-- natively has pgsodium privileges.

-- 1. Expand search_path to include vault and pgsodium schemas
ALTER FUNCTION encrypt_driver_pii(text)  SET search_path = public, vault, pgsodium, pg_temp;
ALTER FUNCTION decrypt_driver_pii(text)  SET search_path = public, vault, pgsodium, pg_temp;

-- 2. Transfer ownership to supabase_admin (has pgsodium privileges on managed Supabase)
ALTER FUNCTION encrypt_driver_pii(text)  OWNER TO supabase_admin;
ALTER FUNCTION decrypt_driver_pii(text)  OWNER TO supabase_admin;
