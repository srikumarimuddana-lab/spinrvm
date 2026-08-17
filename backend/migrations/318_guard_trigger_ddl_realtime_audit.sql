-- A37 fix (ACTION_ITEMS.md, deliberately deferred from A35 —
-- docs/change-log/2026-08-17-a35-retention-guard-monitor.md):
--
-- Migration 317's check_disabled_guard_triggers() and the 6-hourly
-- retention_guard_monitor_loop that calls it are a POLL — they can only
-- observe trigger state at check time. They structurally cannot catch a
-- disable -> mutate/delete -> re-enable cycle completed within a single
-- psql/dashboard session, which is exactly the shape of the 2026-08-14
-- incident that motivated A35 in the first place (confirmed benign, but the
-- detection gap it exposed is real). No polling cadence closes that gap —
-- only a synchronous, event-driven audit does.
--
-- This migration adds that: a `ddl_command_end` event trigger, scoped via
-- `WHEN TAG IN ('ALTER TABLE')` to only ALTER TABLE statements (not every
-- DDL command type database-wide), that re-checks guard-trigger state the
-- instant any ALTER TABLE finishes and writes an append-only audit_logs row
-- if any guard is found disabled AT THAT MOMENT. Because ALTER TABLE ...
-- DISABLE TRIGGER and ALTER TABLE ... ENABLE TRIGGER are two separate DDL
-- statements even inside one session, this fires once at disable time
-- (while the trigger IS disabled, before anything else in the session runs)
-- and again at re-enable time -- so the "disabled for only a few seconds
-- mid-session" case that the 6h poll cannot see becomes a permanent,
-- timestamped audit_logs row the moment it happens, regardless of how
-- quickly it's undone afterward.
--
-- ============================================================================
-- WHY THIS WAS NOT BUILT ALONGSIDE A35, AND WHY IT'S SAFE NOW
-- ============================================================================
-- A35 deferred this specific mechanism (event trigger) for two stated
-- reasons -- both addressed here, not just asserted away:
--
-- 1. "An event trigger fires database-wide for every ALTER TABLE statement,
--    so a mistake in its body risks breaking unrelated migrations
--    repo-wide." Addressed structurally: the ENTIRE body of
--    _audit_guard_trigger_ddl() below is wrapped in a single
--    BEGIN ... EXCEPTION WHEN OTHERS THEN RAISE WARNING ... END block. No
--    code path in this function can ever raise an exception that escapes to
--    the caller -- worst case on any internal bug (missing table, dropped
--    dependency, permission issue) is a RAISE WARNING that gets logged and
--    the triggering ALTER TABLE proceeds completely unaffected. This was
--    verified directly (see change-log for the exact commands run), not
--    just reasoned about: a real Supabase branch was created, this
--    migration applied, and (a) an unrelated ALTER TABLE succeeded cleanly
--    with the event trigger installed, (b) deliberately disabling a guard
--    trigger produced an immediate audit_logs row, (c) re-enabling it
--    produced no error and no spurious row, (d) the function was confirmed
--    to survive being pointed at a nonexistent check_disabled_guard_triggers
--    (by testing the EXCEPTION path directly) without blocking DDL.
--
-- 2. "This session has no way to test one against a live Postgres instance
--    before it would ship to a live-tested production system." Addressed:
--    Supabase database branching (a real, isolated Postgres instance, not a
--    mock) is now available and was used for exactly this purpose --
--    see the change-log's verification section for the branch ID and the
--    commands run against it. This is no longer a hypothetical, untested
--    mechanism; it has been exercised against a real disable/re-enable
--    cycle before landing here.
--
-- Scope discipline, unchanged from A35's posture: this is still detect-only.
-- It never re-enables a trigger, never blocks the ALTER TABLE that disabled
-- one (an operator mid-migration disabling a trigger on purpose should not
-- have this silently fight them), and writes to audit_logs only -- the same
-- table, same append-only guarantee, same "security-relevant event -> audit
-- table + log" convention as every other control in this repo.
--
-- Does NOT itself page/alert (Sentry, CRITICAL log) -- that stays the
-- 6-hourly Python loop's job (retention_guard_monitor.py), which now also
-- surfaces any 'regulatory_guard_trigger_disabled_realtime' audit_logs row
-- written since its last tick, so a same-session disable/re-enable that
-- this event trigger caught still reaches on-call within one poll cycle
-- (worst case ~6h to a live page) even though the PERMANENT RECORD is now
-- instantaneous. True sub-6h paging directly from SQL (e.g. via pg_notify +
-- a LISTEN-ing process, or a Supabase Realtime subscription on audit_logs)
-- is a reasonable future enhancement, deliberately not built here to keep
-- this change's blast radius to "one event trigger, one audit row" and
-- nothing that adds a new always-on network/process dependency to a
-- database-wide DDL hook.
--
-- Known limitation (spinr-migration-reviewer, 2026-08-17), accepted, not
-- fixed here: `WHEN TAG IN ('ALTER TABLE')` scopes by DDL command type only
-- -- Postgres event trigger WHEN clauses can't filter by schema, so this
-- fires on every ALTER TABLE anywhere in the database (Supabase-managed
-- schemas included), not just `public`. The EXCEPTION WHEN OTHERS wrapper
-- keeps this correctness-safe (empirically verified, see below), but every
-- out-of-scope ALTER TABLE still pays the no-op function-call cost. A
-- `pg_event_trigger_ddl_commands()` early-exit on `schema_name <> 'public'`
-- would trim that; deliberately left as a follow-up rather than added here
-- to keep this migration's tested surface area unchanged after review.
--
-- rollback: DROP EVENT TRIGGER IF EXISTS guard_trigger_ddl_audit;
--           DROP FUNCTION IF EXISTS _audit_guard_trigger_ddl();
--           DROP INDEX CONCURRENTLY IF EXISTS idx_audit_logs_action_created;

CREATE OR REPLACE FUNCTION _audit_guard_trigger_ddl()
RETURNS event_trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
DECLARE
    disabled_row RECORD;
    disabled_count INT := 0;
    disabled_json JSONB := '[]'::jsonb;
BEGIN
    -- Everything below is wrapped so this function can NEVER raise an
    -- exception that escapes to the caller -- see the top-of-file comment
    -- for why that's the one non-negotiable property of this function.
    BEGIN
        FOR disabled_row IN
            SELECT * FROM check_disabled_guard_triggers()
        LOOP
            disabled_count := disabled_count + 1;
            disabled_json := disabled_json || jsonb_build_object(
                'table_name', disabled_row.table_name,
                'trigger_name', disabled_row.trigger_name,
                'tgenabled', disabled_row.tgenabled
            );
        END LOOP;

        IF disabled_count > 0 THEN
            -- audit_logs.details is TEXT (production schema, migration 06),
            -- not JSONB -- explicitly cast, do not assume the JSONB shape
            -- some later migration files describe but production never
            -- actually carries (confirmed live via information_schema
            -- before writing this migration).
            INSERT INTO audit_logs
                (id, action, entity_type, entity_id, actor_id, details, created_at)
            VALUES (
                gen_random_uuid()::text,
                'regulatory_guard_trigger_disabled_realtime',
                'database',
                'pg_trigger',
                'system',
                jsonb_build_object(
                    'actor_id', 'system',
                    'actor_role', 'system',
                    'disabled_triggers', disabled_json,
                    'detected_at', now(),
                    'source', 'ddl_command_end_event_trigger',
                    'ddl_tag', tg_tag
                )::text,
                now()
            );
        END IF;
    EXCEPTION WHEN OTHERS THEN
        -- Swallow everything. A bug here must never block the ALTER TABLE
        -- that triggered this function -- see top-of-file comment.
        RAISE WARNING 'guard_trigger_ddl_audit: internal error (swallowed, DDL unaffected): %', SQLERRM;
    END;
END;
$$;

REVOKE EXECUTE ON FUNCTION _audit_guard_trigger_ddl() FROM PUBLIC, anon, authenticated;

COMMENT ON FUNCTION _audit_guard_trigger_ddl IS
    'A37 real-time companion to migration 317''s check_disabled_guard_triggers(). '
    'Fires synchronously at the end of every ALTER TABLE statement (event trigger, '
    'see guard_trigger_ddl_audit below); writes one append-only audit_logs row the '
    'instant any append-only regulatory guard trigger is found disabled. Entire body '
    'is wrapped in EXCEPTION WHEN OTHERS so a bug here can never block a real ALTER '
    'TABLE. Detect-only: never re-enables anything, never blocks the DDL that '
    'disabled a trigger. Escalation (Sentry/CRITICAL log) stays the job of '
    'utils/retention_guard_monitor.py''s 6h loop, which also polls for recent rows '
    'from this event trigger. Created in migration 318.';

DROP EVENT TRIGGER IF EXISTS guard_trigger_ddl_audit;
CREATE EVENT TRIGGER guard_trigger_ddl_audit
    ON ddl_command_end
    WHEN TAG IN ('ALTER TABLE')
    EXECUTE FUNCTION _audit_guard_trigger_ddl();

COMMENT ON EVENT TRIGGER guard_trigger_ddl_audit IS
    'A37: real-time detection companion to migration 317. Scoped to ALTER TABLE '
    'only (not every DDL command type) to minimize blast radius. See '
    '_audit_guard_trigger_ddl() for the guarantee that this can never block DDL.';

-- retention_guard_monitor.py's _fetch_realtime_events() queries this table
-- every 6h with `WHERE action = ? AND created_at >= ? ORDER BY created_at
-- DESC` (spinr-migration-reviewer finding, 2026-08-17). audit_logs already
-- has idx_audit_logs_created (created_at DESC alone) and
-- idx_audit_logs_actor_created ((actor_id, created_at DESC)) from earlier
-- migrations, but nothing covering `action` -- on a live, always-growing,
-- every-admin-action audit table, that means either a seq-scan filtered on
-- `action` or an index scan on created_at re-filtering every row in the
-- lookback window. Add the compound index this query pattern actually needs.
--
-- CONCURRENTLY (matches precedent in migration 114) so this never takes a
-- blocking lock on a live table. NOTE, same operational caveat as migration
-- 317 (see its own top-of-file comment): `CREATE INDEX CONCURRENTLY` cannot
-- run inside a transaction block, and backend/scripts/run_migrations.py
-- wraps every migration file in one transaction (autocommit=False) -- so
-- this statement (and this migration as a whole, since it must stay one
-- file) needs to be applied with an out-of-band autocommit connection, not
-- via the standard runner. Document this for whoever applies it; it is the
-- same manual-apply path 317 already required.
--
-- rollback: DROP INDEX CONCURRENTLY IF EXISTS idx_audit_logs_action_created;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_audit_logs_action_created
    ON audit_logs (action, created_at DESC);
