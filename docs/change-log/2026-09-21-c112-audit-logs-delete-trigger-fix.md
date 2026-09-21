# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (spinrvm session) |
| Surface(s) | backend (database/migrations) |
| Domain (Sentry tag) | admin (background retention job; not a request-path domain) |
| PR / commit link | (see PR opened alongside this file) |
| Related issue or gap ID | ACTION_ITEMS.md C112 |

## 1. Issue / gap identified

`audit_logs`' migration-57 trigger (`audit_logs_no_mutate`, unconditional `BEFORE UPDATE OR
DELETE`) conflicted with migration-56's flag-gated `audit_logs_no_delete` trigger, so the one
sanctioned DELETE path — `purge_pii_retention()`'s Step G, the Saskatchewan Transportation
Act's 7-year `audit_logs` retention ceiling — never actually worked once both migrations were
applied. Separately, the currently-live `purge_pii_retention()` body (migration 335) had never
carried forward the `PERFORM set_config(...)`/exception-handler shape Step G needs at all, so
today's actual failure mode is a **silent no-op** (the DELETE branch is dead code; a `SELECT
COUNT(*)` runs instead), not a crash — confirmed empirically against a real Postgres, not
inferred from the migration text.

## 2. Root cause

Three migrations independently added tamper-evidence triggers to `audit_logs` (51, 56, 57)
without composing them against each other. Migration 57's trigger duplicated 51's UPDATE guard
(already accepted as redundant, per migration 317's own comment) and additionally, unlike 51,
also covered DELETE — colliding with 56's purpose-built, flag-gated DELETE exception. Postgres
fires every applicable `BEFORE ROW` trigger for one statement, not just the first to match, so
56 letting a flagged DELETE through did not stop 57 from aborting it anyway.

## 3. Fix / remediation

One migration (434) ships two parts together, because ACTION_ITEMS.md's own investigation
found that shipping either alone would make things worse (see "Risk" below):

1. Narrows migration 57's `audit_logs_no_mutate` trigger to `BEFORE UPDATE` only (drops
   `DELETE`), leaving migration 56's `audit_logs_no_delete` as the sole DELETE guard. Chosen
   over the alternative (making 57's trigger flag-aware on DELETE too) because it's simpler —
   no duplicated flag-check logic across two triggers — and matches the precedent this
   codebase already accepts (57 being redundant with 51 on the UPDATE side).
2. Re-forks `purge_pii_retention()` verbatim from its current live body (migration 335) with
   only Step G changed: restores the `PERFORM set_config('spinr.audit_logs.allow_delete',
   'true', true)` call before the DELETE, and wraps it in the same `BEGIN...EXCEPTION WHEN
   OTHERS...RAISE...END` shape every sibling gated-delete step (Step H's `financial_events`,
   Step M's `compliance_export_events`) already uses.

## 4. Risk & impact on existing functionality

- **Why both parts had to ship together, not as two separate PRs:** restoring the
  `set_config(...)` call (part 2) *without* either the trigger fix (part 1) or an exception
  handler would have converted today's silent no-op into an **unhandled-exception rollback of
  every other retention step in the same function call** (Steps A–N all run inside one
  `purge_pii_retention()` invocation) — turning a zero-impact dormant bug into an active
  incident the first time the nightly retention job ran after that change. This was the single
  biggest risk in this fix and is why it's one migration, not two.
- **Blast radius:** isolated to `audit_logs`' own triggers and `purge_pii_retention()`'s Step G.
  Grepped for every other reference to `audit_logs_no_mutate`, `_audit_logs_immutable`, and
  `spinr.audit_logs.allow_delete`: only migrations 56/57/434 and the RLS test suite touch them.
  Steps A–F and H–N of `purge_pii_retention()` are carried forward byte-for-byte from migration
  335 (verified by the new static regression test's "carried forward unregressed" checks) — no
  other retention step's logic changes.
- **UPDATE protection on `audit_logs` is unaffected** — migration 51's `audit_logs_no_update`
  trigger, and 57's now-narrower `audit_logs_no_mutate`, both still independently block UPDATE
  for every role including `service_role`.
- **Currently zero observable production impact either way** — `audit_logs` was only created in
  migration 06 (comparatively recent), so no row is anywhere near the 7-year retention
  threshold yet. This fix closes a bug before it can ever actually matter, not one currently
  causing harm.
- **No interaction with the ride state machine, money/wallet deltas, or any request-path
  endpoint** — `purge_pii_retention()` is called only by a scheduled background job.

## 5. User-experience effect

None. This is a database function/trigger definition, invoked only by a scheduled retention
job with no request-path caller. No rider, driver, corporate-admin, or internal-admin sees any
difference, mid-session or otherwise.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/434_fix_audit_logs_delete_trigger_conflict.sql` | New migration: narrows `audit_logs_no_mutate` to UPDATE-only; re-forks `purge_pii_retention()` from 335 with Step G's exception handler restored | The fix itself |
| `backend/tests/test_migration_434_audit_logs_delete_trigger_fix.py` | New static-text regression test (CI has no Postgres for the main suite) pinning both halves of the fix and that other steps are carried forward unregressed | Repo convention (`test_step_*_migration.py` family) for migration-body regression coverage |
| `backend/tests/rls/conftest.py` | Applies migration 434 after 57 in the `audit_logs` section build-out; updated the stale "triggers do not compose safely" comment | Keep the RLS harness reflecting the fixed, current schema |
| `backend/tests/rls/test_audit_and_insurance_correction_rls.py` | Module docstring updated; `test_flag_gated_delete_is_still_blocked_by_migration_57_trigger` renamed to `test_flag_gated_delete_now_succeeds_after_migration_434_trigger_fix` and rewritten to assert success (row actually deleted) instead of the prior exception | Prove the fix against a real Postgres, not just pin the bug |
| `ACTION_ITEMS.md` | C112 (the retention-trigger entry) marked resolved | Backlog hygiene |

## 7. Before / after

```sql
-- Before (migration 57, still live)
CREATE TRIGGER audit_logs_no_mutate
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION _audit_logs_immutable();
```

```sql
-- After (migration 434)
DROP TRIGGER IF EXISTS audit_logs_no_mutate ON audit_logs;
CREATE TRIGGER audit_logs_no_mutate
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION _audit_logs_immutable();
```

```sql
-- Before (Step G, migration 335 — currently live)
IF NOT p_dry_run AND current_setting('spinr.audit_logs.allow_delete', true) = 'true' THEN
    DELETE FROM audit_logs WHERE created_at < v_started_at - c_audit_log_age;
    GET DIAGNOSTICS v_audit_deleted = ROW_COUNT;
ELSE
    SELECT COUNT(*) INTO v_audit_deleted FROM audit_logs WHERE created_at < v_started_at - c_audit_log_age;
END IF;
```

```sql
-- After (Step G, migration 434)
IF NOT p_dry_run THEN
    PERFORM set_config('spinr.audit_logs.allow_delete', 'true', true);
    BEGIN
        DELETE FROM audit_logs WHERE created_at < v_started_at - c_audit_log_age;
        GET DIAGNOSTICS v_audit_deleted = ROW_COUNT;
    EXCEPTION WHEN OTHERS THEN
        PERFORM set_config('spinr.audit_logs.allow_delete', 'false', true);
        RAISE;
    END;
    PERFORM set_config('spinr.audit_logs.allow_delete', 'false', true);
ELSE
    SELECT COUNT(*) INTO v_audit_deleted FROM audit_logs WHERE created_at < v_started_at - c_audit_log_age;
END IF;
```

## 8. Rollback plan

Documented in migration 434's own header comment:
```sql
DROP TRIGGER IF EXISTS audit_logs_no_mutate ON audit_logs;
CREATE TRIGGER audit_logs_no_mutate
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION _audit_logs_immutable();
```
followed by re-applying migration 335's `purge_pii_retention()` definition verbatim. Since
`audit_logs` has no rows anywhere near the 7-year threshold yet, there is no already-purged
data at stake — a pure schema/function rollback, no data-level remediation needed.

## 9. Verification performed

- [x] **Automated tests run against a real Postgres** (started a local PostgreSQL 16 instance
      in this session specifically to verify this fix properly, since it's a regulatory-
      retention trigger interaction that a mocked-Supabase unit test cannot exercise):
      `backend/tests/rls -c /dev/null --confcutdir=tests/rls` — 393/393 passing (baseline before
      this change was also 393/393; this change renames one existing test rather than adding a
      net-new one, so the count matching is expected, not a sign nothing changed — the renamed
      test's body was rewritten from "asserts blocked" to "asserts succeeds", verified by reading
      the diff, not just the count). New static-text migration regression test
      (`test_migration_434_audit_logs_delete_trigger_fix.py`, 15 tests) also passing.
- [x] Blast-radius grep performed: confirmed no other file references
      `audit_logs_no_mutate`/`_audit_logs_immutable`/`spinr.audit_logs.allow_delete` besides
      migrations 56/57/434 and the RLS test suite; confirmed the Python-level
      `test_retention_purge*.py` files only mock the `purge_pii_retention` RPC call, unaffected
      by this SQL-only change.
- [x] Reviewed against relevant CLAUDE.md conventions: append-only migration (new file, no edit
      to 57/335), `migration-override-ok` annotation present for the `CREATE OR REPLACE
      FUNCTION` re-fork, `ruff check`/`ruff format --check` clean on all touched Python files,
      next-available migration number confirmed via `ls backend/migrations | sort -V | tail`.
- [ ] Manual repro against a full Supabase/production-shaped schema — not performed; this
      session's real-Postgres verification used the RLS harness's minimal `audit_logs`-only
      schema (matching this repo's existing RLS test-tier convention), not a full
      `supabase_schema.sql` restore. `purge_pii_retention()`'s other ~19 referenced tables
      (`rides`, `driver_location_history`, `compliance_export_events`, etc.) do not exist in
      that minimal schema, but PL/pgSQL function bodies are not validated against the catalog at
      `CREATE FUNCTION` time (only at invocation) — confirmed empirically: the
      `CREATE OR REPLACE FUNCTION purge_pii_retention(...)` statement itself succeeded in the
      RLS harness despite those tables being absent, and the new trigger/Step-G behavior tests
      (which don't invoke the function directly, only its trigger-level DELETE guard) passed.
      Invoking `purge_pii_retention()` itself end-to-end was not performed.

## What was NOT verified

- Whether migrations 56/57 (and thus this bug) are actually applied to the live production
  `schema_migrations` table — no production DB access from this session, same caveat the
  original ACTION_ITEMS.md C112 entry already stated.
- End-to-end invocation of `purge_pii_retention()` against a full production-shaped schema (see
  above) — only the trigger-composition fix and Step G's SQL shape were verified directly; the
  other 19 steps' behavior is unchanged (carried forward verbatim from 335, confirmed by the
  static regression test), not independently re-verified end-to-end in this pass.
- No staging environment access from this session to run the actual scheduled retention job.
