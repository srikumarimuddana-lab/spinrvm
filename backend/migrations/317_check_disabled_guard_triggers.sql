-- A35 fix (docs/audit/2026-08-16-legacy-ride-count-drop-investigation.md):
-- an ad-hoc, hand-written SQL script (run directly against Postgres, not
-- through any code in this repo) disabled the append-only regulatory guard
-- triggers on driver_insurance_periods/financial_events/audit_logs to
-- hard-delete rows those triggers exist specifically to protect (7-year SGI
-- insurance-period retention, immutable financial ledger, tamper-evident
-- audit trail). Nothing in application code can prevent a raw psql/dashboard
-- SQL session from doing this again — DISABLE TRIGGER is a DDL privilege,
-- not something a BEFORE-trigger can guard against. The only thing code CAN
-- do is detect it, loudly, the next time a background loop runs.
--
-- This function is read-only and dynamic: it scans pg_trigger for every
-- non-internal trigger repo-wide whose name matches our append-only-guard
-- naming convention (%_no_mutate / %_no_delete — driver_insurance_periods_
-- no_mutate, financial_events_no_mutate, audit_logs_no_delete,
-- audit_logs_no_mutate, disputes_no_delete, driver_period_distances_
-- no_mutate, compliance_export_events_no_mutate, and any future one that
-- follows the same naming convention) and returns any that are currently
-- disabled (pg_trigger.tgenabled <> 'O'). No new table, no RLS needed —
-- pure introspection over pg_catalog, callable only by service_role.
--
-- rollback: DROP FUNCTION IF EXISTS check_disabled_guard_triggers();

CREATE OR REPLACE FUNCTION check_disabled_guard_triggers()
RETURNS TABLE (table_name text, trigger_name text, tgenabled text)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
    SELECT
        c.relname AS table_name,
        t.tgname AS trigger_name,
        t.tgenabled::text AS tgenabled
    FROM pg_trigger t
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE NOT t.tgisinternal
      AND n.nspname = 'public'
      AND (t.tgname LIKE '%\_no\_mutate' OR t.tgname LIKE '%\_no\_delete')
      AND t.tgenabled <> 'O'
    ORDER BY c.relname, t.tgname;
$$;

COMMENT ON FUNCTION check_disabled_guard_triggers IS
    'A35 defense-in-depth: read-only scan for any append-only regulatory '
    'guard trigger (naming convention %_no_mutate / %_no_delete) that is '
    'currently disabled. Empty result = all guards intact. Called daily by '
    'utils/retention_guard_monitor.py; loud CRITICAL log + Sentry capture + '
    'audit_logs row on any non-empty result. Created in migration 317.';

REVOKE EXECUTE ON FUNCTION check_disabled_guard_triggers() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION check_disabled_guard_triggers() TO service_role;
