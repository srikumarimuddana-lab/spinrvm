# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides, payments, safety (test-coverage, cross-cutting) |
| PR / commit link | (this commit) |
| Related issue or gap ID | Audit finding N18 / ranked blocker #29, `docs/audit/2026-08-18-full-fleet-whole-app-audit.md`; referenced from `ACTION_ITEMS.md` |

## 1. Issue / gap identified

No test in the entire suite exercised a Postgres Row-Level Security policy
from a real `anon`/`authenticated` role. `backend/tests/conftest.py`'s
`mock_supabase_client` fixture is a mocked Python object — it can validate
that *our code* calls the right Supabase filters, but it cannot validate
that the *database itself* actually enforces the RLS policy those filters
depend on as a backstop. A broken or missing RLS policy would ship green
through the whole existing test suite.

## 2. Root cause

RLS is enforced by the Postgres engine at query time, scoped to the
connecting role. Every existing test connects (indirectly, via the mock)
as if it were the backend's own service-role client, which bypasses RLS by
design — so RLS was structurally unreachable from any test that existed
before this change. This is not a bug in any one test; it's a coverage gap
in the test *architecture* (no fixture ever gave a test a real Postgres
connection with role-switching), which is why closing it required new
infrastructure, not just new test cases.

## 3. Fix / remediation

Added `backend/tests/rls/` — a new, deliberately isolated pytest directory:

- `conftest.py` provisions a real, throwaway Postgres **database** per test
  session (via `CREATE DATABASE`, dropped at teardown) against a real
  Postgres reachable through `TEST_DATABASE_URL` (falling back to
  `DATABASE_URL`). It recreates the `anon` / `authenticated` / `service_role`
  Postgres roles and Supabase's own published `auth.uid()` / `auth.role()` /
  `auth.jwt()` helper functions (these ship with every Supabase project,
  outside this repo's migrations — there's nothing to read verbatim from
  this repo, so they're reproduced from Supabase's own published
  definitions), then applies the **actual shipped SQL this repo tracks**:
  `backend/supabase_schema.sql` (the `users`/`drivers`/`rides` table DDL,
  extracted verbatim by parsing the real file, not hand-copied) and
  `backend/supabase_rls.sql` (applied byte-for-byte, unmodified), plus
  `backend/migrations/58_financial_events.sql`,
  `70_fix_financial_events_rls.sql`, `290_financial_events_grant_lockdown.sql`,
  and `64_driver_insurance_periods.sql`, each read from disk and executed as
  written. If any of these files' policy syntax changes, the tests read the
  changed file directly next run — drift shows up as a real test failure,
  not a stale copy going silently out of sync.
- `as_role(cur, role, claims)` flips the Postgres session to a given role
  with a given JWT claim set via `SET ROLE` + `set_config('request.jwt.claims', ...)`,
  matching Supabase's/PostgREST's own convention (and the convention this
  task was asked to follow).
- 32 tests across `test_core_tables_rls.py` (`users`, `drivers`, `rides`)
  and `test_money_and_safety_rls.py` (`financial_events`,
  `driver_insurance_periods`) assert both positive (owner/admin/service_role
  can act) and negative (another rider/driver/anon cannot) paths for each
  policy, plus two layered cases worth calling out explicitly:
  - `financial_events`: migration 58's INSERT policy is `WITH CHECK (true)`
    (permissive by design) — what actually blocks anon/authenticated from
    forging a ledger row is migration 290's table-level `REVOKE`, a
    *different* layer from RLS. The tests exercise both layers together,
    the way a real PostgREST request would hit them, rather than only
    checking the RLS layer in isolation.
  - `driver_insurance_periods`: migration 64 ships no INSERT policy at all
    for anon/authenticated. Postgres RLS default-denies any command with no
    applicable policy — even though the baseline table-level GRANT (added
    by the test harness to mirror Supabase's own default new-table grants)
    would otherwise permit the INSERT. Asserted directly since it's easy to
    get backwards when reasoning about it from the migration comment alone.
- `pytest.ini`: registered a new `rls` marker (required by the existing
  `--strict-markers` addopt, or the new test files would hard-error under
  the main suite's config instead of self-skipping).
- `CLAUDE.md`'s Testing Conventions gained a new "RLS (DB-role-level)" test
  tier entry with the run command.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** No production code, route, or migration
  changed — this is new test infrastructure only. `pytest.ini`'s one-line
  `markers` addition is additive and does not change existing marker
  behavior (`slow`/`integration`/`unit`/`e2e` untouched).
- **The new tests are excluded from the default coverage gate in practice**:
  `backend/tests/conftest.py` sets `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`
  fakes but no `DATABASE_URL`/`TEST_DATABASE_URL`, so under a normal
  `pytest` run (which does load `backend/tests/conftest.py`, since
  `backend/tests/rls/` sits under `testpaths = tests`) these tests
  self-skip via the `pytestmark = pytest.mark.skipif(...)` in
  `tests/rls/conftest.py` rather than running or failing. This was **not**
  end-to-end verified in this sandbox (see "What was NOT verified" below) —
  it's inferred from reading `conftest.py`'s env setup and the skip
  condition, not from an actual co-collection run alongside the full mocked
  suite (installing all ~149 backend dependencies here was out of scope for
  this change).
- **Who else reads/writes the tables touched by the new SQL applied in the
  fixture**: none — the fixture applies real files but does so into a
  brand-new, uniquely-named, disposable database dropped at teardown. It
  never touches a real `public` schema, any developer's local DB, staging,
  or production. `backend/supabase_schema.sql`/`supabase_rls.sql`
  themselves were read, not modified.
- **Finding surfaced by writing this (not itself fixed here)**: `users`,
  `drivers`, and `rides` — the three tables this task was specifically
  asked to prioritize — have **no RLS policies at all in `backend/migrations/`**.
  Their real policies live only in `backend/supabase_rls.sql`, a file
  applied manually via the Supabase SQL editor (per its own header comment)
  and never run by `backend/scripts/run_migrations.py`. This means there is
  currently no way to fully guarantee `supabase_rls.sql` reflects what's
  actually live in production — it could have been hand-edited in the
  Supabase dashboard without a corresponding commit. This is a pre-existing
  gap this change did not create, but it does make the new tests' fidelity
  guarantee weaker for those three tables than for the migration-file-backed
  ones (`financial_events`, `driver_insurance_periods`): the tests prove
  the *file* enforces the intended access model, not that production is
  currently running that exact file. Recommended follow-up, not done here:
  either commit `supabase_rls.sql`'s statements as a real numbered migration
  (so `run_migrations.py` becomes the source of truth and a diff between
  file and prod becomes checkable) or add an admin-tooling check that
  fetches and diffs live `pg_policies` against this file.

## 5. User-experience effect

None. Test-only change; nothing rider/driver/corporate-admin/internal-admin
facing, and nothing that runs as part of any request path.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/rls/conftest.py` | New. Provisions a throwaway Postgres database, roles, auth shim, and applies real shipped SQL; exposes `as_role()`. | Foundation fixture — nothing like this existed. |
| `backend/tests/rls/test_core_tables_rls.py` | New. 18 tests for `users`/`drivers`/`rides` RLS policies (`backend/supabase_rls.sql`). | Covers the 3 tables named in the task as highest priority. |
| `backend/tests/rls/test_money_and_safety_rls.py` | New. 14 tests for `financial_events` (migrations 58/70/290) and `driver_insurance_periods` (migration 64) RLS + grants. | Covers the money-ledger and safety/regulatory audit tables. |
| `backend/pytest.ini` | Added `rls` to the `markers` list. | `--strict-markers` would otherwise hard-error on the new tests' `pytest.mark.rls` under the main suite's config. |
| `CLAUDE.md` | Added an "RLS (DB-role-level, `backend/tests/rls/`)" entry to the Test tiers list under Testing Conventions, with the run command. | Document how to run these tests locally, per the task's own instruction. |
| `ACTION_ITEMS.md` | Updated the RLS DB-role-level coverage backlog item with partial-progress status, this change-log link, and remaining scope. | Honest status tracking — this closes a slice, not the whole backlog item. |

## 7. Before / after

Pure additive change (new test files + one marker registration + two doc
entries) — no existing behavior-changing diff to show.

## 8. Rollback plan

Delete `backend/tests/rls/`, revert the one-line `markers` addition in
`backend/pytest.ini`, and revert the `CLAUDE.md`/`ACTION_ITEMS.md` doc
edits. No migration, no data, no running system is affected — a plain
`git revert` of this commit is a complete and sufficient rollback (the
exception this file's own template warns about — "a `git revert` is not a
rollback plan for anything already applied to live data" — does not apply
here: nothing here touches live data).

## 9. Verification performed

- [x] **Automated tests run — RLS suite, against a real local Postgres 16
  instance.** This sandbox environment ships Postgres 16.13 binaries with an
  already-initialized cluster (`/var/lib/postgresql/16/main`); started it
  (`service postgresql start`), installed `psycopg2-binary==2.9.12` (pinned
  version already in `backend/requirements.txt`) and `pytest` via
  system-Python `pip install --break-system-packages` (this sandbox has no
  venv with backend's other ~149 dependencies), then ran:
  ```
  export TEST_DATABASE_URL="<local libpq connection string, postgres role>"
  cd backend && python3 -m pytest tests/rls -c /dev/null --confcutdir=tests/rls -v
  ```
  **Result: 32 passed, 0 failed.** (Two iterations were needed to get there:
  the auth-shim `nullif()`/cast ordering initially threw
  `invalid input syntax for type json` for the `anon`/no-claims case, and
  `service_role` initially lacked the same baseline table GRANT as
  anon/authenticated — both fixed in `conftest.py` before the final green
  run above.)
- [x] `ruff check backend/tests/rls/` — all checks passed.
- [ ] Manual repro steps followed in staging — n/a, test-only change.
- [x] Blast-radius grep performed — searched `backend/migrations/*.sql` +
  `backend/supabase_rls.sql` for every `CREATE POLICY`/`ENABLE ROW LEVEL
  SECURITY` statement to confirm no other test file, fixture, or CI
  workflow already defines an `rls` marker, a `pg_conn`/`pg_cur` fixture
  name, or an `as_role` helper that this would collide with — none found.
- [x] Reviewed against `backend/migrations/CLAUDE.md`'s RLS pattern
  conventions and the root `CLAUDE.md`'s "RLS first" migration rule before
  choosing which 5 tables to prioritize.
- [ ] Feature-flagged — n/a, test-only.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; see §8).
- [x] Blast radius is stated: isolated, new test infra only, plus one
  pre-existing gap surfaced (see §4) but not fixed by this change.
- [x] No behavior change to any shipped flow — nothing here runs outside
  `pytest tests/rls`.

---

## Coverage scope — what fraction of the 207 policies this covers

The 2026-08-18 audit finding (N18 / ranked blocker #29) cited "207 policy
statements across 139 migrations." A grep-based recount in this session of
`CREATE POLICY` occurrences across `backend/migrations/*.sql` +
`backend/supabase_rls.sql` (the two places this repo tracks RLS policy SQL)
found **127** literal `CREATE POLICY` occurrences (some of which are a
`DROP POLICY IF EXISTS` + `CREATE POLICY` pair replacing the same named
policy, so the count of *currently effective, distinct* policies is lower
than 127). This session's recount method was not reconciled against the
audit's original method (possibly a live `pg_policies` introspection against
production, which would differ from a static grep) — treat "127" and "207"
as two different counting methods, not a claim that 80 policies vanished.

Of either figure, this change adds real DB-role-level test coverage for
**11 distinct, currently-effective policies across 5 tables**:

| Table | Policies covered | Source |
|---|---|---|
| `users` | `users_select_self`, `users_update_self`, `users_delete_self` | `backend/supabase_rls.sql` |
| `drivers` | `drivers_select_public`, `drivers_update_self` | `backend/supabase_rls.sql` |
| `rides` | `rides_select_parties`, `rides_insert_rider`, `rides_update_parties` | `backend/supabase_rls.sql` |
| `financial_events` | `financial_events_insert` (58), `financial_events_select` (70, superseding 58's) — plus migration 290's table-level GRANT/REVOKE, not itself an RLS policy but load-bearing for the same access-control outcome | migrations 58, 70, 290 |
| `driver_insurance_periods` | `driver_insurance_periods_select` | migration 64 |

**This is roughly 5–9% of the cited totals (11/207 ≈ 5%, 11/127 ≈ 9%
depending on which denominator is used) — a deliberate, honestly partial
start, not closure of ranked blocker #29.** The five tables were chosen for
consequence (money ledger, SGI-regulated safety audit trail, and the three
core consumer-facing tables named in the task), not for ease.

## What was NOT verified

- **Not verified against production's actual live policy set** — only
  against the files this repo tracks in git (`backend/supabase_rls.sql`,
  `backend/migrations/58,64,70,290`). As noted in §4, `supabase_rls.sql` is
  applied manually via the Supabase SQL editor and is not run by
  `run_migrations.py`, so there is no automatic guarantee the file in this
  repo is what's currently live. A stale or hand-edited production policy
  would not be caught by these tests.
- **Not run in CI** — no workflow file was added or changed to wire
  `TEST_DATABASE_URL` and run `pytest tests/rls` automatically; these tests
  will silently self-skip in the existing CI pipeline (no Postgres service
  container configured for it) until that's added. That wiring is
  explicitly out of scope for this change and is called out as remaining
  work in `ACTION_ITEMS.md`.
- **Full-suite co-collection was not run end-to-end** — this sandbox has
  none of the backend's ~149 other Python dependencies installed (no
  FastAPI, no `supabase-py`, etc.), so `pytest` (the full default command,
  which would also collect `backend/tests/rls/` under the same
  `backend/tests/conftest.py`) could not be executed here to directly
  confirm the skip-not-error behavior claimed in §4. That behavior is
  inferred from reading the skip condition and `conftest.py`'s env setup,
  not observed directly.
- **The remaining ~116–196 policies (depending on denominator) across the
  other ~130+ migration files are untouched** — no claim of coverage is
  made for any table outside the five listed above. Candidates flagged
  during this session's survey as good next targets, by rough policy count
  per table: `lost_and_found`/`lost_and_found_messages` (4 policies each),
  `referral_payouts` (4), `auto_payout_batches` (4), `complaints` (2),
  `refresh_tokens`/`stripe_events`/`schema_migrations` (deny-all policies,
  migration 26).
- **`otp_records`, `settings`, `support_tickets`, `faqs`, `vehicle_types`,
  `fare_configs`, `service_areas`** were created in the test fixture only as
  minimal stub tables (an `id` column, sometimes `user_id`) so
  `backend/supabase_rls.sql` could be applied unmodified without errors on
  missing relations — these stubs are not full schema replicas and their
  own policies (`otp_deny_all`, `settings_deny_all`, `tickets_select_own`,
  etc.) are exercised structurally by loading without error but were **not**
  given dedicated test cases in this change; they're a byproduct of making
  the harness able to run the real file, not a coverage claim for those
  tables.
