-- Migration 205: EXECUTE lockdown on migration-50 wallet RPCs (follow-up to 203)
--
-- Context: wallet_increment_balance and wallet_transfer (migration 50) are
-- SECURITY DEFINER money functions, and Supabase exposes every public
-- function at /rest/v1/rpc/. Migration 111 locked down their sibling
-- wallet_pay_for_ride ("Gap 3 — No EXECUTE restriction on PostgREST roles"),
-- but these two from the same file were never REVOKEd, so any client holding
-- the anon key can call them directly with an arbitrary wallet_id, bypassing
-- RLS and all application-layer auth. Surfaced during the migration-203
-- review — same class of exposure 203 closes for the corporate RPCs.
-- Verified no migration after 50 redefines either function (comment-only
-- references in 107-110, 179, 196), so the signatures below match the live
-- definitions.
--
-- No function body changes — grants only. Forward-compatible: REVOKE/GRANT
-- are catalog-only, safe to run against live traffic.
--
-- Rollback:
--   GRANT EXECUTE ON FUNCTION wallet_increment_balance(UUID, NUMERIC) TO PUBLIC;
--   GRANT EXECUTE ON FUNCTION wallet_transfer(UUID, UUID, NUMERIC) TO PUBLIC;

-- Revoking PUBLIC also strips service_role's inherited EXECUTE; the backend's
-- service-role key must keep calling these (utils/referral_payout.py,
-- routes/loyalty.py, repositories/wallet_repo.py, routes/fare_split.py,
-- services/corporate_allowance_service.py), so grant it back explicitly —
-- same pattern as migrations 196/203/204.
REVOKE EXECUTE ON FUNCTION wallet_increment_balance(UUID, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION wallet_increment_balance(UUID, NUMERIC)
    TO service_role;

REVOKE EXECUTE ON FUNCTION wallet_transfer(UUID, UUID, NUMERIC)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION wallet_transfer(UUID, UUID, NUMERIC)
    TO service_role;
