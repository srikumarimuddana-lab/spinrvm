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
-- follows the same naming convention) plus one explicitly named legacy
-- exception (audit_logs_no_update, migration 51 — predates the naming
-- convention; a later, redundant audit_logs_no_mutate from migration 57
-- also blocks UPDATE, so this exception isn't load-bearing today, but the
-- scan should still see it directly rather than relying on that overlap).
-- No new table, no RLS needed — pure introspection over pg_catalog,
-- callable only by service_role.
--
-- Checks tgenabled = 'D' specifically (disabled), not merely <> 'O' —
-- Postgres also uses 'R' (replica-only) and 'A' (ALWAYS, i.e. hardened to
-- fire even in replica-role sessions — *more* protective than the 'O'
-- default). Alerting on 'A' would be a false positive on a trigger someone
-- deliberately made stricter.
--
-- Known limitation (documented, not solved by this migration): this is a
-- point-in-time poll, not an event-based audit. It can only observe a
-- trigger that is disabled AT CHECK TIME. A disable -> DELETE -> re-enable
-- cycle completed within one psql/dashboard session — the exact shape of
-- the A35 incident this fix responds to — can finish well inside any
-- realistic polling interval and this function will see nothing wrong on
-- every subsequent check. A synchronous `ddl_command_end` event trigger
-- recording every ALTER TABLE ... {DIS,EN}ABLE TRIGGER on a regulated table
-- would close that gap; none exists yet (tracked as ACTION_ITEMS.md A37 —
-- deliberately not built in this change: an event trigger fires
-- database-wide for every ALTER TABLE statement, so a mistake in its body
-- risks breaking unrelated migrations repo-wide, and this session has no
-- way to test one against a live Postgres instance before it would ship to
-- a live-tested production system).
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
      AND (
          t.tgname LIKE '%\_no\_mutate'
          OR t.tgname LIKE '%\_no\_delete'
          OR t.tgname = 'audit_logs_no_update'
      )
      AND t.tgenabled = 'D'
    ORDER BY c.relname, t.tgname;
$$;

COMMENT ON FUNCTION check_disabled_guard_triggers IS
    'A35 defense-in-depth: read-only scan for any append-only regulatory '
    'guard trigger (naming convention %_no_mutate / %_no_delete, plus the '
    'named legacy exception audit_logs_no_update) that is currently '
    'disabled (tgenabled = ''D'' specifically, not merely <> ''O'' -- see '
    'top-of-file comment on the ''A''/ALWAYS false-positive risk). Empty '
    'result = all guards intact. KNOWN LIMITATION: point-in-time poll, '
    'cannot see a disable-act-reenable cycle completed between checks -- '
    'see top-of-file comment and ACTION_ITEMS.md A37. Called every 6h by '
    'utils/retention_guard_monitor.py; loud CRITICAL log + Sentry capture + '
    'audit_logs row on any non-empty result. Created in migration 317.';

REVOKE EXECUTE ON FUNCTION check_disabled_guard_triggers() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION check_disabled_guard_triggers() TO service_role;
