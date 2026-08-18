# Change Impact & Risk Log — A35 retention-guard-bypass fix

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend (new SQL function, new background loop, new plan-builder service, new tests) |
| Domain (Sentry tag) | admin (closest fit — this is a database-security/regulatory-posture control, not a product domain) |
| Related | Closes `ACTION_ITEMS.md` **A35** (surfaced 2026-08-16 while investigating A34's 224-vs-186 ride-count discrepancy) |

## 1. Issue/gap identified

An ad-hoc, hand-written SQL script — run directly against Postgres (dashboard SQL editor or `psql`, never a file in this repo) — disabled the append-only regulatory guard triggers on `driver_insurance_periods`/`financial_events`/`audit_logs` in order to hard-delete rows those triggers exist specifically to protect (7-year SGI insurance-period retention, immutable financial ledger, tamper-evident audit trail). The 2026-08-14 runs targeted genuine pre-launch test accounts and were confirmed benign, but the script itself had no eligibility check — unlike the sanctioned `purge_pii_retention()` Step H, which explicitly refuses to hard-delete any account carrying `rides`/`driver_insurance_periods`/`payouts`/`bank_accounts` rows.

## 2. Root cause

No sanctioned, codebase-resident tool existed for one-off test-account cleanup, so whoever needed it hand-wrote SQL against the live database instead of routing through the existing DSAR/retention machinery — and that hand-written SQL had no guard at all.

## 3. Fix/remediation — two parts, deliberately not one

**3a. Detection (closes the "we'd never know if this happened to a real driver" gap):** `backend/migrations/317_check_disabled_guard_triggers.sql` adds one read-only `SECURITY DEFINER` function, `check_disabled_guard_triggers()`, that dynamically scans `pg_trigger` for any trigger matching this repo's append-only-guard naming convention (`%_no_mutate` / `%_no_delete`) that is currently disabled. `backend/utils/retention_guard_monitor.py` calls it every 6 hours from a new background loop and, on any hit, writes a CRITICAL log line, a Sentry `fatal`-level capture, and one `audit_logs` row — pure detection, zero mutation, no auto-re-enable (an operator mid-migration disabling a trigger on purpose shouldn't have a bot silently undo it — that's its own hazard). Wired into `core/lifespan.py`'s spawn list and the loop watchdog's tracked-name list (the earlier full-fleet audit found 13 existing loops missing from that list — this one is added correctly from day one).

**3b. Prevention-by-substitution (closes the "why did anyone need ad-hoc SQL in the first place" gap):** `backend/services/test_account_cleanup_service.py` is a plan-builder — same posture as every other legacy-migration tool this week (`legacy_payout_correction_service.py`, `legacy_gst_backfill_service.py`): read-only end to end, no DELETE, no trigger touched. It resolves a phone-number list to accounts and buckets each into `safe_to_delete` or `blocked_regulated_data_present`, using the exact same four-table eligibility guard as `purge_pii_retention()` Step H (rides, driver_insurance_periods, payouts, bank_accounts). A blocked account is the headline of the report, never silently omitted. **No delete-execution path is built** — wiring one is a separate, later, explicitly-gated change, consistent with every sibling tool.

**Explicitly out of scope, and why:** nothing in application code can prevent a direct Postgres session (dashboard SQL editor, `psql`) from disabling a trigger — `ALTER TABLE ... DISABLE TRIGGER` is a DDL privilege exercised entirely outside any request path this backend controls. 3a is the loudest defense that's actually possible; 3b removes the *reason* anyone would reach for raw SQL again.

## 4. Risk & impact on existing functionality

- **Blast radius of the migration**: additive only — one new function, zero existing objects touched, zero data touched. `REVOKE`/`GRANT` only scope the new function itself.
- **Blast radius of the loop**: read-only RPC call + (on a hit) a log line, a Sentry event, and one `audit_logs` insert. Never touches `driver_insurance_periods`/`financial_events`/`audit_logs`/any trigger. Failure mode if the migration hasn't been applied yet: the RPC call raises, caught by the loop's own `try/except`, logged as an error, tick returns `{"disabled": 0, ...}` — **cannot false-positive as "all clear" nor false-negative as "disabled" from a missing function**, it just logs a DB error like any other RPC-not-found case until the migration lands.
- **Blast radius of the cleanup service**: no callers anywhere in the codebase yet (not wired into any route/CLI/loop) — inert until a future PR builds an execute path against it.
- **Who else reads `driver_insurance_periods`/`payouts`/`bank_accounts`/`rides`**: only read (`SELECT ... LIMIT 1`), by the new service, for eligibility checking. No write path exists to interact with.
- **PII in the loop's own output**: the `audit_logs` row and Sentry payload carry table names and trigger names only — no user data, no PII. The cleanup service's report prints `user_id`/`driver_id`/phone (already-normalized `+1XXXXXXXXXX`) for operator review of a plan the operator themselves supplied the phone numbers for — same exposure class as every other dry-run report in this repo this week.

## 5. User experience effect

None. No rider/driver/corporate-admin/internal-admin screen reads any of this. Pure backend security/observability control plus an unwired tool.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/317_check_disabled_guard_triggers.sql` | New — read-only `SECURITY DEFINER` function | Introspection point the monitor loop calls |
| `backend/utils/retention_guard_monitor.py` | New — 6-hourly detect-only background loop | Closes A35's detection gap |
| `backend/services/test_account_cleanup_service.py` | New — dry-run plan builder, Step H eligibility guard | Closes A35's "why hand-write SQL" gap |
| `backend/core/lifespan.py` | +14 lines: spawn the new loop, add its name to `_WATCHDOG_LOOP_NAMES` | Wire the loop in correctly, including watchdog coverage from day one |
| `backend/tests/test_retention_guard_monitor.py` | New — 7 tests | Escalation, dedupe, fail-open, never-raises |
| `backend/tests/test_test_account_cleanup_service.py` | New — 8 tests | Bucketing correctness, multi-reason reporting, no driver-row edge case |
| `docs/change-log/2026-08-17-a35-retention-guard-monitor.md` | This file | Mandatory Change Impact Log for a regulatory/security-adjacent change |

## 7. Before/after

Before: no automated way to detect a disabled regulatory guard trigger; the only sanctioned account-deletion path was `purge_pii_retention()`'s 7-year Step H, with no lighter-weight *safe* option for one-off test-account cleanup — the gap that produced the risky ad-hoc script in the first place.

After: any future disable of a `%_no_mutate`/`%_no_delete` trigger is caught within 6 hours (Sentry `fatal` + CRITICAL log + audit row); test-account cleanup has a sanctioned, reviewed, guarded plan-builder to reach for instead of hand-written SQL.

## 8. Rollback plan

`git-revert-safe` for the code (loop, service, tests). The migration's own top-comment rollback: `DROP FUNCTION IF EXISTS check_disabled_guard_triggers();` — safe at any time, the loop degrades to logging an RPC-not-found error every 6h (no false signal either direction) if the function is ever removed while the loop is still deployed.

## 9. Verification performed

- [x] `pytest backend/tests/test_retention_guard_monitor.py backend/tests/test_test_account_cleanup_service.py` — 15/15 pass
- [x] `ruff check` / `ruff format --check` clean on all new/changed files
- [x] `python -c "import ast; ast.parse(...)"` syntax check on `core/lifespan.py` after the edit
- [x] Manual review pass: `spinr-migration-reviewer` (migration conventions, SECURITY DEFINER boilerplate, trigger-scan correctness) + `spinr-regulatory-compliance-checker` (does this actually close A35, eligibility-guard parity with Step H) — see PR comments for verdicts
- [x] **Correction (2026-08-17, later same day): migration WAS subsequently applied to production**, superseding the note directly above (kept, struck through in spirit, not deleted, so this doc stays an honest record of what was true at each point rather than quietly rewritten). Discovering how to apply it surfaced `ACTION_ITEMS.md` **A39**: `scripts/migrate.py` — the runner this repo's own docs pointed at — does not match production's actual `schema_migrations` shape (`filename`/`checksum`/`applied_at`/`applied_by`, migration 24's shape) and would fail immediately if run; `scripts/run_migrations.py` is the one actually live. Docs corrected in commit `0556b85`. Re-verified live, independently, in this review pass:
  ```sql
  select proname, prosecdef, proconfig from pg_proc where proname = 'check_disabled_guard_triggers';
  -- {"proname":"check_disabled_guard_triggers","prosecdef":true,"proconfig":["search_path=public, pg_catalog"]}
  select * from check_disabled_guard_triggers();
  -- [] (empty — healthy, no disabled guard triggers currently)
  ```
  Function exists, `SECURITY DEFINER` correctly set, `search_path` correctly pinned, callable, and reports the expected healthy (empty) result on today's production trigger set.
- [ ] Not exercised end-to-end against a live *disabled-trigger* scenario — the query above confirms the healthy/empty path works against real production data, but no test disabled a real trigger and re-ran the query to confirm the alerting path fires correctly outside the unit-test mocks. Deliberately not attempted: intentionally disabling a regulatory guard trigger against production, even briefly, to test the alarm is a bad trade against the risk it's meant to catch.

## 10. What was NOT verified

- Whether the migration, once applied, correctly excludes every legitimate non-guard trigger repo-wide via the `%_no_mutate`/`%_no_delete` name pattern — reasoned from the existing trigger names found via grep (`driver_insurance_periods_no_mutate`, `financial_events_no_mutate`, `audit_logs_no_delete`, `audit_logs_no_mutate`, `disputes_no_delete`, `driver_period_distances_no_mutate`, `compliance_export_events_no_mutate`), not confirmed against a live `pg_trigger` listing.
- The test_account_cleanup_service's actual delete-execution path — does not exist, by design, in this change.

## 11. Review findings — verdicts and what was applied

**`spinr-migration-reviewer`: safe to merge**, with two non-blocking notes, both applied before this section was written:
1. `tgenabled <> 'O'` would false-positive-alert on `'A'` (`ENABLE ALWAYS` — *more* protective than baseline, not a bypass). → Narrowed to `tgenabled = 'D'` specifically, with the reasoning documented in the migration's top comment.
2. The naming-convention scan misses one pre-existing legacy trigger, `audit_logs_no_update` (migration 51, predates the naming convention) — currently harmless because a redundant, newer trigger (`audit_logs_no_mutate`, migration 57) also blocks UPDATE, but the scan's own claim to be exhaustive wasn't accurate. → Added `audit_logs_no_update` as an explicitly named exception in the `WHERE` clause and documented why.

**`spinr-regulatory-compliance-checker`: adequate partial fix**, with three findings:
1. **The detection loop cannot catch a disable→act→re-enable cycle completed within one session** (the actual shape of the 2026-08-14 incident) — polling, at any cadence, only observes state at check time. A real fix needs a synchronous `ddl_command_end` event trigger. → Did **not** build the event trigger in this change (database-wide blast radius on every future `ALTER TABLE`, untestable against live Postgres from this session — see CLAUDE.md's "escalate, don't silently ship" rule). Instead: corrected both the migration's and the loop's docstrings to state this limitation plainly rather than imply it's solved, and opened **`ACTION_ITEMS.md` A37** as a dedicated, deliberately-deferred follow-up with the reviewer's own proposed design.
2. **`test_account_cleanup_service.py` claimed an "exact" match to Step H's eligibility guard; it's actually a superset** — it added a `rides.driver_id` check that Step H itself does not have. → Corrected the module and function docstrings to state this precisely (superset, not exact match), and opened **`ACTION_ITEMS.md` A38** for the latent gap this reveals in Step H itself (not fixed here — Step H is money/regulatory-adjacent production code and deserves its own dedicated review, not a drive-by edit inside this fix).
3. **`print_report()` prints unmasked phone numbers** — acceptable for its current CLI-only, operator-supplied-the-numbers usage (not a log/Sentry/analytics violation as currently used), but a real risk if ever automated. → Added an explicit docstring warning that any future caller piping this into a log aggregator, CI artifact, or webhook needs its own PIPEDA review first.

Re-verified after applying all of the above: `pytest` still 15/15, `ruff check`/`format --check` still clean.
