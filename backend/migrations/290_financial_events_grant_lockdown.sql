-- 290_financial_events_grant_lockdown.sql
--
-- SECURITY FIX: block direct PostgREST writes to the 7-year tax ledger.
--
-- Rollback: GRANT INSERT, UPDATE, DELETE ON financial_events TO authenticated;
-- GRANT ALL ON financial_events TO anon;  -- (restores the hole; do not do this)
--
-- ============================================================================
-- The hole
--
-- migration 58 created financial_events with:
--     CREATE POLICY financial_events_insert ON financial_events
--         FOR INSERT WITH CHECK (true);
--
-- No TO clause (so it applies to PUBLIC — anon AND authenticated) and no
-- accompanying REVOKE of the table-level grants Supabase hands new
-- public-schema tables by default. RLS policies only narrow what a role may
-- touch; they do not remove the underlying GRANT. A permissive policy plus a
-- default grant equals an open door.
--
-- Impact: any holder of the publishable anon key — which ships inside
-- rider-app and driver-app — could POST /rest/v1/financial_events and forge a
-- row in the ledger that CRA/SK 7-year record-keeping and SOC2 CC9.1 rest on.
-- event_type is CHECK-constrained to the enum, but ride_id is nullable,
-- metadata is free-form jsonb, delta_cents is unconstrained, and the user_id FK
-- only requires the target user to EXIST — so the forged row can be attributed
-- to any account, not just the attacker's own.
--
-- This has been live since migration 58. It is not introduced by PR #3464;
-- it was found by that PR's security + migration audits, which both flagged
-- that hardening the DELETE path (289) and the settlement write path (288)
-- while INSERT stays wide open would create false confidence in an "append-only
-- tamper-evident ledger" that anyone with the anon key can append to.
--
-- Why this repo already knows the pattern
--
-- migration 142 retrofitted exactly this fix onto `disputes` plus nine
-- corporate money tables, calling them "direct money-mutation vectors via
-- PostgREST". migration 151 did the same for subscription_payments, with the
-- comment: "Without this, any authenticated anon-key JWT could
-- INSERT/UPDATE/DELETE payment rows." Neither sweep included financial_events
-- — 142 enumerated its tables by name and this one was not on the list.
--
-- What stays working
--
-- The backend writes exclusively through service_role, which bypasses both RLS
-- and these grants, so every legitimate write path (ledger_service.record_event,
-- settle_ride_card_payment, purge_pii_retention) is unaffected. SELECT is
-- preserved for authenticated because migration 58/70's SELECT policy already
-- scopes it correctly (own rows, or admin) — revoking it would break that
-- legitimate read. Verified no client bundle queries either table directly
-- (grep across rider-app/driver-app/admin-dashboard/shared: zero hits).
--
-- Forward-compatible: GRANT changes only. No schema change, no data touched,
-- no locks beyond the catalog row. Safe against live traffic.
-- ============================================================================

REVOKE ALL ON financial_events FROM anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON financial_events FROM authenticated;
GRANT  SELECT ON financial_events TO authenticated;

COMMENT ON TABLE financial_events IS
    'Append-only money ledger. UPDATE always blocked by trigger '
    'financial_events_no_mutate; DELETE blocked except inside '
    'purge_pii_retention() Step H (migration 289), which sets '
    'spinr.financial_events.allow_delete=true around the 7-year DSAR '
    'hard-delete. Direct PostgREST writes are revoked from anon/authenticated '
    '(migration 290) — the RLS INSERT policy from migration 58 is permissive, '
    'so the table-level GRANT is what actually gates writes. All writes go '
    'through service_role. Required by CRA record-keeping (7-year retention) '
    'and SOC2 CC9.1. Created in migration 58.';

NOTIFY pgrst, 'reload schema';
