-- Migration 78: Pin search_path on SECURITY DEFINER PII functions
-- Rollback: ALTER FUNCTION encrypt_driver_pii(text) RESET search_path;
--           ALTER FUNCTION decrypt_driver_pii(text) RESET search_path;
--
-- Why: SECURITY DEFINER functions without search_path can be exploited via
-- objects injected into a user-writable schema (CVE pattern: schema confusion).
-- Pinning to public,pg_temp ensures only intended objects are resolved.

ALTER FUNCTION encrypt_driver_pii(text) SET search_path = public, pg_temp;
ALTER FUNCTION decrypt_driver_pii(text) SET search_path = public, pg_temp;
