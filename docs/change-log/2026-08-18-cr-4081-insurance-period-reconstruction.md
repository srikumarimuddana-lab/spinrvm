# Change Impact & Risk Log — CR #4081: legacy-imported ride insurance-period reconstruction

**Date:** 2026-08-18
**Files:** `backend/migrations/332_backfill_legacy_ride_insurance_periods.sql`, `backend/tests/test_migration_332_backfill_legacy_ride_insurance_periods.py`, `ACTION_ITEMS.md`

## Issue/gap identified

186 legacy-imported rides (all `status = 'completed'`) had zero `driver_insurance_periods` rows — the SGI/Saskatchewan Transportation Act 7-year audit trail for TNC insurance-coverage-layer proof was structurally absent for every one of them. Called a BLOCKER-level gap in the 2026-08-15 dual-run cutover audit, blocking a safe old-app decommission (tentative Oct 31, 2026).

## Root cause

`booking_import_service.py` writes imported `rides` rows directly, bypassing the normal ride-state-machine transitions that trigger `backend/utils/insurance_periods.py::record_period_transition()`. Migration 65's existing backfill only ever covered drivers/rides that were *currently* online/in-progress at migration-64-deploy time — it never touched historical imported rides, since they were neither.

## Fix/remediation

**Decision (CR #4081, recorded 2026-08-18):** reconstruct-and-flag, chosen by this session's user after confirming (via explicit `AskUserQuestion`) they hold the specific SGI-facing legal/regulatory authority CLAUDE.md requires for this call — not proceeded on a general instruction alone, consistent with an earlier point in this session where a subagent correctly declined to act on this same CR under a claimed-but-unverifiable "already approved" instruction.

**Implementation, migration 332:**
1. Additive column `driver_insurance_periods.is_reconstructed BOOLEAN NOT NULL DEFAULT FALSE` — every pre-existing (contemporaneously-logged) row correctly defaults to `false`.
2. Extended the migration-64 append-only immutability trigger to also lock `is_reconstructed` against post-insert mutation (the original trigger's column-by-column comparison couldn't have included a column that didn't exist yet).
3. Backfilled `driver_insurance_periods` for **182 of the 186** rides: Period 2 (`driver_arrived_at` → `started_at`) and Period 3 (`started_at` → `ride_completed_at`, carrying `ride_id`), both rows marked `is_reconstructed = true`.
4. **4 of the 186 rides deliberately excluded, not silently dropped** — documented by ride ID in the migration's own header comment:
   - 3 rides have `driver_id IS NULL` — `driver_insurance_periods.driver_id` is `NOT NULL` with a `drivers(id)` FK; there is no driver to attribute a period to.
   - 1 ride has a `driver_id` but `driver_arrived_at`/`started_at`/`ride_started_at` are all `NULL` — only `created_at` (2026-04-13 19:46) and `ride_completed_at` (2026-04-14 10:34, ~14.8 hours later) exist. That gap is far too long to be a real trip duration; reconstructing a Period 3 boundary from it would be fabrication, not reconstruction. Flagged for manual review if this specific ride is ever needed.

**Disclosed limitation (not a bug — inherited from source data, same as an existing precedent):** `driver_notified_at`/`driver_accepted_at`/`assigned_at` are `NULL` for all 186 rides — the old app/importer never captured a driver-assignment timestamp. Migration 65's own Period-2 backfill already falls back through `COALESCE(driver_arrived_at, driver_accepted_at, created_at, now())` for exactly this reason; migration 332 follows the same established convention. Per `spinr-insurance-period-auditor`'s own rule ("Period 2 starts on `driver_assigned`, not `driver_accepted`"), this means the reconstructed Period 2 start (`driver_arrived_at`) understates the true (unrecorded) Period 1→2 boundary for all 182 rows. This is explicit in the migration's header comment and here, not buried.

## Risk & impact on existing functionality

**Blast radius — who else reads/writes `driver_insurance_periods`, grepped:**
- `backend/utils/insurance_periods.py::record_period_transition()` — the only writer for live transitions. Untouched by this migration; its own row-level `INSERT`s are unaffected by a new nullable-with-default column.
- `backend/migrations/65_backfill_driver_insurance_periods.sql` — already-applied, historical migration. Not re-run, not touched.
- Admin/regulator export surfaces and `spinr-insurance-period-auditor` (an audit-only Claude agent, not application code) — both should now treat `is_reconstructed` as the discriminator; no application code currently reads this column (it's new), so nothing downstream silently starts behaving differently. A future PR wiring an admin UI or SGI export to surface `is_reconstructed` is a natural follow-up, not required by this migration.
- The append-only immutability trigger (`_driver_insurance_periods_immutable()`) — re-created via `CREATE OR REPLACE FUNCTION`, same trigger object from migration 64, no re-attachment needed. Verified the DELETE-block, close-transition-only, and existing 6-column comparison are all preserved verbatim, with `is_reconstructed` added as a 7th.
- The `driver_insurance_periods_open` partial unique index (one open row per driver, `WHERE ended_at IS NULL`) — every row this migration inserts has `ended_at` set (all 182 rides are `status = 'completed'`, historical). No reconstructed row is ever left open, so this migration cannot collide with a driver's real, currently-open period row, regardless of how many legacy rides a given driver appears in.

**Additive over destructive:** new column with a safe default, new rows only — no existing row's `period`, `started_at`, `ended_at`, `ride_id`, or `driver_id` is touched. No migration/table is repurposed.

## User experience effect

None — no rider/driver/corporate-admin-facing surface reads this table today. Internal-admin: none yet (no admin UI change in this migration); a future SGI-export or admin-tooling PR consuming `is_reconstructed` would be the first user-facing surface, out of scope here.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/migrations/332_backfill_legacy_ride_insurance_periods.sql` | New additive migration: `is_reconstructed` column, extended immutability trigger, 2-statement backfill (Period 2 + Period 3) for 182/186 legacy rides | Implements CR #4081's approved reconstruct-and-flag decision |
| `backend/tests/test_migration_332_backfill_legacy_ride_insurance_periods.py` | 15 new migration-SQL-contract tests | Pin the column, trigger extension, idempotency guard, open-row-never, and the 4-ride exclusion list against regression |
| `ACTION_ITEMS.md` | A34 bullet updated from "STILL OPEN" to "RESOLVED (2026-08-18)" with the decision, migration reference, and the disclosed Period-2 limitation | Keep the tracking doc accurate |

## Before/after

Before: `driver_insurance_periods` has 0 rows for any of 186 legacy-imported rides.
After: 364 new rows (182 × 2 periods), all `is_reconstructed = true`; 4 rides remain with 0 rows, explicitly documented as excluded rather than silently absent.

## Rollback plan

```sql
DELETE FROM driver_insurance_periods WHERE is_reconstructed = true;
ALTER TABLE driver_insurance_periods DROP COLUMN is_reconstructed;
```
Safe any time before a downstream consumer (SGI export, admin tooling) relies on the reconstructed rows. Reverting the trigger function back to its migration-64 form is optional once the column is gone (the `is_reconstructed` comparison simply becomes dead code referencing a dropped column — should be reverted for cleanliness, but isn't a correctness blocker for the rollback itself). This is **not** a `git revert`-only rollback — it's a real data change on production regulatory records, so the DELETE above is the actual rollback action, documented here per CLAUDE.md's requirement that a rollback plan for anything touching live data be more than "git revert."

## Verification performed

1. Confirmed the 186-row scope directly against production (read-only, via Supabase MCP): `count(*) = 186` for rides with `legacy_import_metadata` and zero existing period rows.
2. Pulled the real `rides` table schema (no guessed column names) to identify the actual available timestamp columns (`driver_notified_at`, `driver_accepted_at`, `driver_arrived_at`, `started_at`/`ride_started_at`, `ride_completed_at`, `assigned_at`).
3. Confirmed `driver_notified_at`/`driver_accepted_at`/`assigned_at` are `NULL` for all 186 rows (0/186) — this is what drove the "follow migration 65's established fallback precedent" decision, not a new judgment call.
4. Identified the exact 4 exception rides by ID via a targeted query (`driver_id IS NULL OR driver_arrived_at IS NULL OR started_at IS NULL OR ride_started_at IS NULL`), confirmed `started_at`/`ride_started_at` are byte-identical for all 186 rows (`IS DISTINCT FROM` count = 0).
5. Confirmed all 182 "clean" rides' `driver_id` resolves to a real row in `drivers` (no FK-violation risk).
6. **Dry-ran the migration's exact `SELECT` logic (read-only, no `INSERT`) against production** before writing the file's final form: Period 2 candidate count = 182, Period 3 candidate count = 182, matching the expected exclusion of exactly the 4 documented rides.
7. Verified chronological ordering (`driver_arrived_at <= started_at <= ride_completed_at`) holds for all 182 rides — 0 rows would produce an invalid (end-before-start) interval.
8. `python3 backend/scripts/check_migration.py` — all hard checks pass (one expected, benign warning: the rollback-plan comment's own `DROP COLUMN` text is flagged by the checker's naive dangerous-ops text scan; it's inside a `--` comment, not live DDL).
9. 15 migration-SQL-contract tests, `backend/tests/test_migration_332_backfill_legacy_ride_insurance_periods.py` — see PR for pass/fail results.

## What was NOT verified

- **The actual `INSERT` was never executed against production from this session.** Every verification above is read-only (`SELECT`, `count(*)`, schema introspection). The migration will run through this repo's normal `run_migrations.py` deploy pipeline, the same path every other migration this session went through — not executed directly via the Supabase MCP tool, deliberately, given the stakes (append-only, 7-year regulatory retention, cannot be casually undone once downstream consumers exist).
- No `spinr-insurance-period-auditor` agent pass was run against this diff as a nested subagent call (not available from a top-level session context the way it is from within a dispatched Agent call) — instead, its documented rules (period definitions, append-only contract, Period-2-starts-on-assignment rule) were applied directly and are cited by name throughout this log and the migration's own comments.
- Whether an admin UI or SGI-export surface should be updated to *display* `is_reconstructed` is intentionally out of scope for this migration — flagged as a natural follow-up, not assumed to be this PR's job.
- Real production data can change between this verification (2026-08-18, this session) and whenever the migration actually runs — the migration's own `NOT EXISTS` guards make it idempotent and safe to re-verify counts immediately before deploy if a large gap in time passes.
