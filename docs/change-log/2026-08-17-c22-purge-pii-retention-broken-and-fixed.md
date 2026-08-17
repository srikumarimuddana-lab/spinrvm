# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend (Supabase Postgres function, applied directly to production) |
| Domain (Sentry tag) | admin / rides (regulatory retention, spans multiple tables) |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | ACTION_ITEMS.md C22 (migration-tracking drift), A38 (migration 321, driver-ride guard) |

## 1. Issue / gap identified

Picking up C22 ("`scripts/migrate.py`'s tracking table doesn't match what's actually live") led to using the Supabase MCP tool to verify production's `schema_migrations` state directly. That surfaced three distinct, escalating findings, all confirmed live against project `soavhtdhefowwvforzwb`:

1. **Tracking-table drift**: `schema_migrations` only records 161 of 407 repo migration files. The table itself was only bootstrapped 2026-08-14 (160 `backfill-verified` rows + 1 manual apply), and the bootstrap batch stopped at migration ~239 — everything after that (108 files) and a scattered ~138 pre-window files with non-strictly-numeric naming were never recorded.
2. **A live schema gap, not just a tracking gap**: spot-checking confirmed migration 321 (A38's regulatory PII-retention fix — merged and marked CLOSED in `ACTION_ITEMS.md`) had never actually been applied to production. `purge_pii_retention()`'s Step H still lacked the `rides.driver_id` guard live, despite the fix being merged weeks earlier. (Other spot-checked migrations — 286, 297 — *were* live despite not being tracked, confirming this is a mix of real application gaps and pure bookkeeping gaps, not one uniform cause.)
3. **A much more serious, previously-invisible bug, found while verifying #2**: after applying migration 321 and running a `purge_pii_retention(true)` dry-run to confirm it, the call failed at **Step D** — `ride_messages.created_at` does not exist (the table has a `timestamp` column instead, per migration 98). Fixing that and re-running surfaced a second, identical bug at **Step F** — `stripe_events.created_at` does not exist either (the table has `received_at`, per migration 22). Postgres does not validate column references inside a `plpgsql` function body at `CREATE`-time, only at execution — so this was invisible in every code review, every migration-reviewer pass, and every previous session, until the function actually ran end-to-end.

## 2. Root cause

`purge_pii_retention()` (originally migration 50) was written assuming `ride_messages` and `stripe_events` might not exist yet on a fresh environment, so it defined its own `CREATE TABLE IF NOT EXISTS` versions of both with `created_at` columns. On this project, both tables already existed under earlier migrations (98 and 22 respectively) with different column names (`timestamp`, `received_at`) — so the `IF NOT EXISTS` clauses were no-ops, and the function's own `created_at` references were left dangling. This is the exact same bug class migration 187 already found and fixed for `driver_location_history` (`recorded_at` vs `received_at`) in 2026-08-10 — the pattern simply wasn't checked for the other tables at the time.

Because a `plpgsql` function body is not type/column-checked until it actually executes a given branch, and because the retention loop's failures are only visible via `logger.exception` in application logs (not a loudly-surfaced Sentry alert distinct from any other exception), this went undetected through however many days the loop has been failing.

## 3. Fix / remediation

- **Migration 321 (A38) applied to production** — the merged-but-never-deployed regulatory PII-retention fix now matches what's actually live.
- **Migration 323**: re-forked `purge_pii_retention()` from 321, changed Step D's `ride_messages` filter from `created_at` to `timestamp` (the real column, quoted since `timestamp` is a keyword-adjacent identifier). Added `idx_ride_messages_timestamp_purge` since the equivalent index from migration 50 (`idx_ride_messages_created_at`) also silently never landed for the same reason.
- **Migration 324**: re-forked from 323, changed Step F's `stripe_events` filter from `created_at` to `received_at` (the real column). A live column-existence sweep against every other table/column the function references (rides, driver_location_history, refresh_tokens, audit_logs, users, drivers, driver_insurance_periods, payouts, bank_accounts, financial_events, saved_addresses, support_tickets, reconciliation_discrepancies, ride_routes, ai_messages, ai_conversations, surge_pricing, price_searches, compliance_export_events) confirmed no further broken references — Steps D and F were the last two.
- **Verified fixed**: `SELECT purge_pii_retention(true)` now completes end-to-end without error and returns real counts, including a genuine backlog: `surge_pricing_deleted: 189208`, `refresh_tokens_deleted: 51` (everything else 0, consistent with this being a pre-launch/low-volume environment for most retention categories).
- **Did NOT execute a live (non-dry-run) purge** — that's a separate decision. The existing daily background loop (`utils/retention_purge.py`, ~03:00 UTC) will pick this up naturally on its next scheduled tick now that the function is healthy.
- All three production changes (321, 323, 324) were applied directly via the Supabase MCP tool after explicit user confirmation (`AskUserQuestion`, twice — once for 321, once for the broader Step D/F fix), per this repo's "confirm first" convention for hard-to-reverse/outward-facing production changes.

## 4. Risk & impact on existing functionality

- **Blast radius: `purge_pii_retention()` and its two callers.** Grepped: only `backend/utils/retention_purge.py` calls this RPC (the daily background loop). No route/service directly invokes it. `routes/admin/*` has no manual-trigger endpoint for it (confirmed via grep for `purge_pii_retention` across `routes/`).
- **Who else reads/writes the affected tables**: `ride_messages` (chat, read/written throughout the ride-messaging flow — this fix only affects the 90-day-old backlog, not live message flow), `stripe_events` (webhook idempotency dedupe table, `claim_stripe_event`/`mark_stripe_event_processed` — this fix only deletes rows older than 90 days, well past any webhook-retry window Stripe itself uses).
- **Direction of change is corrective and additive**: the function could not execute past Step D before; now it can execute all of Steps A-N. Every step's own filter logic is otherwise unchanged from the prior version (321/323) — verified via `spinr-migration-reviewer`.
- **A38's Step H fix (migration 321) is preserved unchanged** through both 323 and 324 — confirmed via the new regression tests' `TestStepHFixCarriedForwardFromMigration321` class.
- **First real (non-dry-run) execution of this function will clear the entire multi-week/month backlog in one pass** — most significantly, ~189K stale `surge_pricing` rows. This is a large single DELETE; the daily loop already runs under a Redis leader lock and there's no other consumer racing it, but the first live tick after this fix will take measurably longer than a normal day's incremental cleanup. Not mitigated further in this change — flagged for whoever monitors the loop's next run.
- **No RLS, permission, or schema-shape change** — `REVOKE`/`GRANT` lines carried forward verbatim in both new migrations.

## 5. User-experience effect

None directly rider/driver/admin-facing — this is a backend-only, unauthenticated-surface fix (the retention function has no HTTP endpoint). Indirect effect: this closes a real regulatory-compliance gap (3-year GPS anonymization, 7-year hard-delete ceilings, PIPEDA right-to-delete enforcement) that had been silently non-functional.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/323_purge_pii_retention_step_d_ride_messages_column_fix.sql` | New — fixes Step D's `ride_messages.created_at` → `timestamp`, adds `idx_ride_messages_timestamp_purge` | The daily retention loop failed at Step D every tick |
| `backend/migrations/324_purge_pii_retention_step_f_stripe_events_column_fix.sql` | New — fixes Step F's `stripe_events.created_at` → `received_at` | Same bug class, one step further into the function |
| `backend/tests/test_step_d_ride_messages_column_fix_migration.py` | New — textual SQL-contract pin (CI has no live Postgres), same convention as `test_step_h_driver_rides_guard_migration.py` | Regression coverage for the Step D fix |
| `backend/tests/test_step_f_stripe_events_column_fix_migration.py` | New — same convention, plus regression checks that Steps D and H's prior fixes carried forward correctly through the re-fork | Regression coverage for the Step F fix and non-regression of 321/323 |
| `ACTION_ITEMS.md` | C22 entry corrected/closed; this finding documented | Accuracy — the original C22 finding was about tracking drift, not this deeper live-execution bug |
| `docs/change-log/2026-08-17-c22-purge-pii-retention-broken-and-fixed.md` | This file | Mandatory Change Impact Log |

## 7. Before / after

```sql
-- Before (migration 321, Step D)
DELETE FROM ride_messages
WHERE created_at < v_started_at - c_chat_age;   -- column does not exist
```

```sql
-- After (migration 323, Step D)
DELETE FROM ride_messages
WHERE "timestamp" < v_started_at - c_chat_age;
```

```sql
-- Before (migration 323, Step F)
DELETE FROM stripe_events
WHERE created_at < v_started_at - c_stripe_event_age;   -- column does not exist
```

```sql
-- After (migration 324, Step F)
DELETE FROM stripe_events
WHERE received_at < v_started_at - c_stripe_event_age;
```

## 8. Rollback plan

Each migration's own header states: re-apply the prior version's `purge_pii_retention()` definition verbatim (321 → undoes 323's Step D fix; 323 → undoes 324's Step F fix). Both are pure `CREATE OR REPLACE FUNCTION` + (323 only) an additive index — no data was mutated by applying these fixes themselves (only the subsequent dry-run `SELECT`, which mutates nothing). Reverting restores the known-broken state (Step D or Step F failing), not a new failure mode. **Not applicable / does not need a "second deploy" rollback plan for data already moved** — no live (non-dry-run) purge was executed as part of this change; the eventual first real purge run is a separate, later event with its own (already-established) semantics (idempotent, time-window-filtered deletes, same as every prior day this loop was intended to run).

## 9. Verification performed

- [x] `SELECT purge_pii_retention(true)` (dry-run) against production, both before and after each fix — confirmed the exact failure point each time, confirmed clean completion after 324.
- [x] Live column-existence sweep against every table/column the function references — confirmed no further broken references beyond Steps D and F.
- [x] `pytest backend/tests/test_step_d_ride_messages_column_fix_migration.py backend/tests/test_step_f_stripe_events_column_fix_migration.py backend/tests/test_step_h_driver_rides_guard_migration.py backend/tests/test_retention_purge.py backend/tests/test_retention_purge_coverage.py backend/tests/test_deletion_hard_delete_migration.py backend/tests/test_pipeda_30day_profile_scrub_migration.py -q --no-cov` — 78/78 pass.
- [x] `ruff check` + `ruff format --check` on both new test files — clean.
- [x] Migration filename numbering checked for duplicates (`323`, `324`) — none.
- [x] `spinr-migration-reviewer` review requested for both migrations before PR creation.
- [x] Both production changes applied only after explicit user confirmation via `AskUserQuestion` (once for migration 321, once for the Step D/F investigation-and-fix).
- [ ] **Not verified**: whether the daily background loop's next real (non-dry-run) tick actually completes successfully and clears the backlog — this change only proves the function itself is now correct via dry-run; the live loop's next scheduled run (or a manually triggered live run) is the actual end-to-end proof and was deliberately not triggered in this session.
- [ ] **Not verified**: whether any other Postgres function in this codebase has the same "table pre-existed under a different column name" bug class — this investigation was scoped to `purge_pii_retention()` specifically, triggered by the C22 pickup; a broader sweep of every `SECURITY DEFINER` function against live column names was not performed.

## What was NOT verified

- The real-world performance/duration of the first live purge run against the ~189K-row `surge_pricing` backlog — not executed, only the dry-run COUNT was observed.
- Whether `schema_migrations`' broader tracking gap (161/407 files, the original C22 finding) needs a full reconciliation — explicitly out of scope for this session per C22's own text ("reconciling which of ~300 migration files have actually landed on production... is a substantial, higher-stakes audit... not a quick fix"). Only migrations 321/323/324 were individually verified and applied.
- Whether any other daily/scheduled background loop in `backend/core/lifespan.py` has a similar silently-failing bug — not investigated, flagged as a natural follow-up given this pattern was found once already (187) and now twice more (323/324) in the same function.
