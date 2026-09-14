# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | rides |
| PR / commit link | see PR description this file is linked from |
| Related issue or gap ID | ACTION_ITEMS.md C49 (progressed, not closed); ACTION_ITEMS.md C118 (new finding, not fixed) |

## 1. Issue / gap identified

`backend/tests/rls/` — the real-Postgres, DB-role-level RLS test tier — had zero
coverage for three tables backing fraud/dispute detection on ride billing
*distance*: `ride_distance_integrity_events` (migration 246),
`ride_distance_recomputes` (242), and `ride_location_gap_events` (237, + 370's
additive `status`-value widening). This is the latest themed slice of the
long-running ACTION_ITEMS.md C49 backlog item ("no test in the entire suite
exercises an RLS policy from a real Postgres `anon`/`authenticated` role").

## 2. Root cause

Not a bug — a coverage gap. These three tables were created across several
migrations (237/242/246/370) with real RLS policies that were never exercised
by a role-switching test, same as ~28 other tables this backlog item has not
yet reached.

## 3. Fix / remediation

Added `backend/tests/rls/test_ride_distance_integrity_rls.py` (43 tests) and
extended `backend/tests/rls/conftest.py` to apply the real shipped migration
SQL for these three tables verbatim into the throwaway test database (same
established idiom as every prior C49 round — see conftest.py's own
docstring). Each table's policies are exercised for both the denied path
(`anon`/`authenticated`, including `admin`/`super_admin` — no admin-override
policy exists on any of the three, proved rather than assumed) and the
allowed path (`service_role`, which carries `BYPASSRLS`). Also added
regression tests for each table's CHECK constraints (`ride_distance_integrity_events.kind`'s
5-value allow-list, `ride_distance_recomputes.new_actual_distance_km >= 0`,
`ride_location_gap_events.status`'s 3-value allow-list including migration
370's additive `unresolved_at_completion`) and for
`ride_location_gap_events`' unique `(ride_id, gap_started_at)` index (the
replay-safety guarantee `route_gap_monitor.py`'s own code comment cites).

No production code changed. This PR is test-coverage-only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped every `.py`/`.sql`/`.ts`/`.tsx` file
  repo-wide for all three table names (see the ACTION_ITEMS.md C49 entry for
  the full list). Production request-path reads/writes for all three go
  through `db_supabase` (the service-role client) from
  `utils/distance_integrity.py`, `utils/route_finalizer.py`,
  `utils/route_gap_monitor.py`, and `utils/retention_purge.py`. Two
  non-request-path consumers exist and were named, not silently omitted:
  `scripts/analyze_ride_route.py` (a read-only diagnostic CLI) and
  `scripts/delete_test_driver_accounts.sql` (a manual superuser cleanup
  tool) — neither is part of the RLS-gated request surface. No
  admin-dashboard/rider-app/driver-app/shared file references any of the
  three tables.
- Nothing in this change touches `backend/tests/conftest.py` (the separate,
  mocked-Supabase test tier every other backend test uses) or any production
  code path. `backend/tests/rls/conftest.py` is additive-only: a new SQL
  application block, a `GRANT` statement naming only the 3 new tables (does
  not touch any earlier `REVOKE`), and 3 new entries appended to the
  `pg_cur` fixture's `TRUNCATE` list.
- No interaction with `backend/core/lifespan.py`'s background loops, the ride
  state machine, or money/wallet deltas — this tier only proves RLS policy
  *logic* against a throwaway database (see conftest.py's own "what this tier
  proves — and doesn't" section, ACTION_ITEMS.md C108); it makes no request
  against the real Supabase project and changes no production schema.

## 5. User-experience effect

None. Backend-only, test-coverage-only change. No rider/driver/corporate-
admin/internal-admin-facing behavior changes in any way, mid-session or
otherwise.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/rls/conftest.py` | Applied migrations 237/370/242/246 verbatim (new tables + RLS policies), added a by-name `GRANT` for the 3 new tables, added them to `pg_cur`'s `TRUNCATE` list, updated the module docstring's coverage-scope paragraph | Extend the real-Postgres RLS harness to cover the 3 new tables, matching the established idiom |
| `backend/tests/rls/test_ride_distance_integrity_rls.py` (new) | 43 new DB-role-level RLS tests across the 3 tables | New themed slice of ACTION_ITEMS.md C49 |
| `ACTION_ITEMS.md` | New C49 status entry (this round); new C118 entry (finding, not fixed) | Track progress and flag the trigger-enforcement gap found while writing these tests |

## 7. Before / after

Not applicable — this is a pure test-coverage addition with no existing
caller and no behavior change. No before/after snippet required per the
template's own "skip for pure additive code" note.

## 8. Rollback plan

`git revert` is sufficient and safe here — this PR adds no migration, no
production code, no config/flag, and touches no live data. Reverting the
commit removes the new test file and reverts `conftest.py`/`ACTION_ITEMS.md`
to their prior state; nothing else is affected.

## 9. Verification performed

- [x] Automated tests run: the full `backend/tests/rls/` suite, including the
  43 new tests — `export TEST_DATABASE_URL="<local rlstest cluster DSN, port 5544>" && cd backend && python3 -m pytest tests/rls -c /dev/null --confcutdir=tests/rls -q` → **369 passed, 0 failed**, against a real local
  Postgres 16 (`rlstest` cluster on port 5544, isolated from the
  pre-existing `main` cluster — its auth config was never touched, per this
  backlog item's own standing constraint). `ruff check` and
  `ruff format --check` both pass clean on the new/changed files.
- [ ] Manual repro steps followed in staging — not applicable, no production
  code changed.
- [x] Blast-radius grep performed: see section 4 above.
- [x] Reviewed against relevant CLAUDE.md convention(s): RLS (this entire PR
  is RLS test coverage), Testing Conventions ("every auth/RLS policy (both
  allowed and denied paths)"), task-decomposition (themed 3-table slice,
  matching prior rounds' sizing).
- [ ] Feature-flagged — not applicable, test-only change with no runtime
  behavior.
- **Review used:** `spinr-security-auditor` via the Agent/Task tool was
  unavailable in this session's isolated-worktree environment (checked via
  tool search, confirmed absent). Used the CLAUDE.md-sanctioned fallback,
  `/code-review` at high effort, against the actual diff. It found two real
  issues, both fixed before this commit: (1) the blast-radius bullet's first
  draft omitted `scripts/analyze_ride_route.py` as a reader of two of the
  three tables — corrected; (2) the test file's `_seed_driver` helper was
  defined but never used — fixed by adding a test that exercises
  `ride_location_gap_events.driver_id` against a real seeded driver row. A
  third observation (diff size exceeds CLAUDE.md's ~200-line batch-size
  guidance) was flagged but not treated as an error: every prior C49 round
  in this backlog item has the same shape (one migration-application block +
  one matching test file, sized to the tables' migration SQL, not to an
  arbitrary line count) — see PR body for the explicit call-out.

## What was NOT verified

- Not run against CI's own `postgres:15` service container — a real local
  Postgres 16 was used instead, same caveat every prior round of this
  backlog item has carried (this harness's own migration SQL has no
  version-specific syntax, so the risk is believed low but not eliminated).
- Whether migrations 237/242/246/370 have actually been applied to
  production's live `schema_migrations` table is not confirmed here — no
  production DB access from this environment.
- The C118 finding (no anti-tamper trigger on 2 of the 3 tables) is
  documented and reproduced against real Postgres, but whether it's worth
  fixing (versus an accepted risk) was not decided here — flagged for a
  human/future session per CLAUDE.md's escalation gate.

## Assumptions

- Migration 238 (`trip_route_integrity_retention.sql`) was deliberately NOT
  applied to the test harness: it `ALTER`s `ride_routes`, a table outside
  this harness's build scope (applying it verbatim would raise
  `UndefinedTable`), and its only touch on `ride_location_gap_events` is a
  plain index plus a reference inside `purge_trip_route_geometry()` —
  neither an RLS policy nor a CHECK constraint under test here. This
  mirrors the precedent already established for skipping migrations 280/315
  against `safety_incidents` in an earlier C49 round (same "not the
  migration that defines the policies" reasoning).
- A real local Postgres 16 (this session's `rlstest` cluster) is
  representative of CI's `postgres:15` service container for RLS-policy
  purposes — same assumption every prior C49 round has made; none of the
  applied SQL uses version-specific syntax.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (and corrected once, honestly, per
  the adversarial review above rather than left wrong)
- [x] No silent behavior change to an already-shipped flow — there is no
  behavior change at all, this is test-coverage-only
