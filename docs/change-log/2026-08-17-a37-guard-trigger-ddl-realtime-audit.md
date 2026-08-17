# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/a37-guard-trigger-ddl-audit` |
| Related issue or gap ID | ACTION_ITEMS.md A37 (deferred from A35) |

## 1. Issue / gap identified

A35 (migration 317 + `retention_guard_monitor.py`) added a 6-hourly *poll* that
detects an append-only regulatory guard trigger
(`driver_insurance_periods_no_mutate`, `financial_events_no_mutate`,
`audit_logs_no_delete`, etc.) left disabled. A poll can only observe state at
check time — it structurally cannot see a `disable → mutate/delete →
re-enable` cycle completed inside one psql/dashboard session between two
polls. That's exactly the shape of the 2026-08-14 incident that motivated A35
(confirmed benign, but the detection gap it exposed was real and undefended).

## 2. Root cause

No synchronous, event-driven hook existed on `ALTER TABLE ... {DIS,EN}ABLE
TRIGGER` — only the periodic RPC scan. A35 explicitly deferred building one
for two reasons: (1) an event trigger fires database-wide for every `ALTER
TABLE`, so a bug in its body risked breaking unrelated migrations repo-wide,
and (2) this session had no way to test one against a live Postgres instance
before it would ship to a live-tested production system.

## 3. Fix / remediation

Migration 318 adds a `ddl_command_end` event trigger
(`guard_trigger_ddl_audit`), scoped to `WHEN TAG IN ('ALTER TABLE')` only, that
re-runs migration 317's `check_disabled_guard_triggers()` at the instant any
`ALTER TABLE` finishes and writes one append-only `audit_logs` row
(`action = 'regulatory_guard_trigger_disabled_realtime'`) if any guard is
found disabled at that moment. The entire function body is wrapped in a
single `EXCEPTION WHEN OTHERS THEN RAISE WARNING ...` block so no internal
bug can ever escape and block the triggering DDL. `retention_guard_monitor.py`
was extended with `_fetch_realtime_events()`, which polls `audit_logs` for
recent rows of that action and merges them into the same per-`(table,
trigger)` dedupe/escalation path as the existing RPC-based state poll — so a
same-session disable/re-enable now reaches on-call within one 6h poll cycle
(instead of never), while the permanent audit record is instantaneous.

Both of A35's stated blockers were addressed directly, not asserted away:
empirically verified against a real, isolated Supabase database branch
(project ref `ewwxlftxrsyejaogdujh`, branch id
`1f4455dc-66a4-45d0-afb6-01c1fbcc38a1`, now deleted) — see §9.

## 4. Risk & impact on existing functionality

- **Blast radius: database-wide but scoped.** The event trigger fires on
  *every* `ALTER TABLE` statement across the entire database (all schemas
  reachable by `ddl_command_end`, filtered `WHEN TAG IN ('ALTER TABLE')`) —
  i.e. every future migration in `backend/migrations/` that uses `ALTER
  TABLE` will invoke this function. This is the one genuinely repo-wide
  surface this change touches.
- Mitigated structurally: the function's entire body sits inside `BEGIN ...
  EXCEPTION WHEN OTHERS THEN RAISE WARNING ... END`, so no code path can raise
  past the caller. Verified empirically (not just by inspection) that even
  with its dependency (`check_disabled_guard_triggers()`) deliberately
  dropped mid-test, a subsequent `ALTER TABLE` still succeeded — see §9.
- Only other reader of `audit_logs` rows with this specific `action` value is
  the new `_fetch_realtime_events()` in `retention_guard_monitor.py` itself;
  no other consumer of `audit_logs` filters or joins on this action, so no
  other code path is affected by the new row shape.
- `retention_guard_monitor.py`'s existing RPC-based path (`_check()`'s
  `state_rows`), `_already_alerted_recently` (Redis dedupe), and `_escalate`
  (Sentry/CRITICAL log) are unchanged in behavior for the state-poll source —
  only extended to also accept rows from the new source through the same
  dedupe key. Grepped for other callers of `_check()` /
  `retention_guard_monitor_loop()`: only `core/lifespan.py`'s startup-loop
  registration (background-loop list, unchanged call signature).
- No ride state machine, wallet, or Stripe interaction. Detect-only: never
  re-enables a trigger, never blocks the DDL that disabled one.

## 5. User-experience effect

None. Backend-only, internal-admin/on-call facing (an earlier CRITICAL log +
Sentry page, same escalation channel A35 already established). No rider,
driver, or corporate-admin-visible surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/318_guard_trigger_ddl_realtime_audit.sql` | New migration: `_audit_guard_trigger_ddl()` function + `guard_trigger_ddl_audit` event trigger on `ddl_command_end` | Synchronous detection to close the poll-interval gap A35 left open |
| `backend/utils/retention_guard_monitor.py` | Added `_fetch_realtime_events()`; rewrote `_check()` to merge state-poll + realtime-event rows through one dedupe/escalation path; updated module docstring | Surface migration 318's realtime audit rows to the existing 6h alerting loop |
| `backend/tests/test_retention_guard_monitor.py` | Extended `patched` fixture with `db.get_rows` mock; added 5 tests for the realtime-event merge/dedupe/failure-isolation behavior | Regression coverage for the new merge logic |

## 7. Before / after

```python
# Before (backend/utils/retention_guard_monitor.py, _check())
async def _check() -> dict[str, int]:
    stats = {"disabled": 0, "alerted": 0, "deduped": 0}
    try:
        state_rows = await db.rpc("check_disabled_guard_triggers", {}) or []
    except Exception as exc:
        logger.error(...)
        state_rows = []
    stats["disabled"] = len(state_rows)
    if not state_rows:
        return stats
    to_alert = [...]  # dedupe over state_rows only
```

```python
# After
async def _check() -> dict[str, int]:
    stats = {"disabled": 0, "alerted": 0, "deduped": 0, "realtime_events": 0}
    try:
        state_rows = await db.rpc("check_disabled_guard_triggers", {}) or []
    except Exception as exc:
        logger.error(...)
        state_rows = []

    realtime_rows = await _fetch_realtime_events()
    stats["realtime_events"] = len(realtime_rows)

    all_rows = list(state_rows) + realtime_rows
    stats["disabled"] = len(all_rows)
    if not all_rows:
        return stats
    to_alert = [...]  # same dedupe path, now over both sources
```

## 8. Rollback plan

- **Code path** (`retention_guard_monitor.py`): `git revert` is sufficient —
  this file has no persisted state of its own; reverting drops back to
  RPC-only detection with no data loss (migration 318's audit rows remain
  harmless, unread history).
- **Migration 318** (database object): explicitly reversible, stated at the
  top of the migration file —
  ```sql
  DROP EVENT TRIGGER IF EXISTS guard_trigger_ddl_audit;
  DROP FUNCTION IF EXISTS _audit_guard_trigger_ddl();
  ```
  This is a pure schema-object rollback (function + event trigger), not a
  data migration — no backfill, no dual-read window needed. Dropping the
  event trigger takes effect immediately with zero risk to in-flight `ALTER
  TABLE` statements (Postgres DDL is transactional; the trigger simply stops
  being invoked for subsequent statements).
- No feature flag needed: this is a detect-only, append-only-audit-row
  mechanism with no user-facing behavior to gate.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_retention_guard_monitor.py -v --no-cov` — 12/12 passed (7 pre-existing + 5 new for the A37 merge behavior).
- [x] `ruff check` + `ruff format --check` on both touched Python files — clean.
- [x] Real-Postgres verification via an isolated Supabase database branch (project `ewwxlftxrsyejaogdujh`, branch `1f4455dc-66a4-45d0-afb6-01c1fbcc38a1`, real hourly cost, user-confirmed, branch deleted immediately after testing to stop the cost clock):
  1. Built a minimal harness matching production's real `audit_logs` schema (confirmed live via `information_schema.columns` that `details` is `TEXT`, not `JSONB`, contrary to what an earlier migration file implied) plus a dummy guard-trigger table.
  2. Applied migrations 317 and 318 verbatim on the branch.
  3. An unrelated `ALTER TABLE` (adding a column to an unrelated table) succeeded cleanly with the event trigger installed — no interference.
  4. `ALTER TABLE ... DISABLE TRIGGER <guard>` produced an immediate, correctly-shaped `audit_logs` row.
  5. `ALTER TABLE ... ENABLE TRIGGER <guard>` immediately after produced no error and no spurious row.
  6. Deliberately dropped `check_disabled_guard_triggers()` (the function's dependency) on the branch, then ran another `ALTER TABLE` — confirmed the `EXCEPTION WHEN OTHERS` path fired (`RAISE WARNING`, visible in branch logs) and the `ALTER TABLE` still completed successfully, proving the "can never block DDL" guarantee holds even under real internal failure, not just under inspection.
- [x] Blast-radius grep performed: searched for all callers of `_check()`, `retention_guard_monitor_loop()`, and all readers/writers of `audit_logs` filtering on `action` — only `core/lifespan.py`'s background-loop registration and the new `_fetch_realtime_events()` itself.
- [x] Reviewed against relevant CLAUDE.md conventions: append-only guard pattern, `SECURITY DEFINER` + pinned `search_path`, observability convention (security-relevant event → audit table + info log, escalation → Sentry/CRITICAL), background-loop replay-safety (idempotent — inserts a fresh timestamped row per detection, no state mutated by the read side).
- [ ] Not run: manual staging repro against the real production Supabase project (see §10 — intentionally scoped to the isolated branch instead, to avoid touching live-tested infrastructure for a database-wide DDL hook).

## 10. What was NOT verified

- Not exercised against the actual production Supabase project — verification used an isolated branch with a hand-built minimal schema (real `audit_logs` shape + a dummy guard-trigger table), not a full clone of production's schema/triggers. The specific append-only guard triggers this protects
  (`driver_insurance_periods_no_mutate`, `financial_events_no_mutate`, etc.)
  were not individually exercised on the branch — only a structurally
  identical dummy trigger following the same naming convention that
  `check_disabled_guard_triggers()` (migration 317) already matches on.
- No load/concurrency test of the event trigger under a high rate of
  concurrent `ALTER TABLE` statements (not a realistic production pattern for
  this repo — migrations run sequentially via `run_migrations.py`, not
  concurrently — so this was judged low-risk and not worth the added branch
  cost/time to test).
- Sentry escalation itself (`_escalate()`'s `sentry_sdk.capture_message` call)
  was not re-verified end-to-end against a real Sentry project in this
  change — that code path is unchanged from A35 and was verified then; this
  change only adds a second row-source feeding into the same, already-tested
  escalation call.
- No automated visual/snapshot regression tooling applies here (backend-only,
  no UI surface) — not applicable, not a gap being silently skipped.
