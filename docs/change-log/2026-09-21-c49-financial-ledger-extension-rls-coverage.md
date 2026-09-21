# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (session `session_01173usfHtfdzMMzYpWeWmVm`) |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | payments |
| PR / commit link | see PR description this file is linked from |
| Related issue or gap ID | ACTION_ITEMS.md C49 (progressed, not closed); ACTION_ITEMS.md C129 (new finding, not fixed) |

## 1. Issue / gap identified

`backend/tests/rls/` had zero coverage for three tables extending or sitting
directly alongside the already-covered `financial_events` money ledger:
`financial_event_entries` (migration 286), `reconciliation_discrepancies`
(migration 59), and `subscription_payments` (migration 151). Latest themed
slice of the long-running ACTION_ITEMS.md C49 backlog item.

Separately: this backlog item's own published "remaining table" fraction had
drifted stale. Two other backlog items (C107/migration 430, C123 phase
1/migration 432) added real RLS coverage for 4 tables
(`disputes`, `cloud_messages`, `push_tokens`, `document_requirements`) as a
side effect of unrelated fixes, without either updating C49's own running
total. Caught before picking this round's slice, not after.

## 2. Root cause

Not a bug — a coverage gap, same as every other C49 round. The stale-total
issue is a process gap: nothing enforces that RLS coverage added under a
different backlog item's PR also updates C49's tracker.

## 3. Fix / remediation

Re-audited the true covered/remaining split directly off disk rather than
trusting the last-published fraction: swept every `backend/tests/rls/*.py`
file for tables actually exercised in a SQL statement (46 covered), and swept
every `CREATE POLICY ... ON <table>` across `backend/migrations/*.sql` **and
`backend/supabase_rls.sql`** multiline-aware, plus migration 27's dynamic
loop (9 tables). Found the prior round's own sweep undercounted by 10 (it
swept only `backend/migrations/`, missing `supabase_rls.sql`'s 10
policy-bearing tables, even though `conftest.py` itself applies that file)
and miscounted migration 27's loop as 6 tables instead of 9. Corrected total:
**71 policy-bearing tables, 46 covered, 25 remaining** — see the ACTION_ITEMS.md
C49 entry for the full corrected remaining-table list, which supersedes every
earlier round's list.

Picked `financial_event_entries`/`reconciliation_discrepancies`/
`subscription_payments` from the corrected list — the highest-stakes theme
remaining per CLAUDE.md's money-path coverage tier — deliberately not
`agent_action_log` (429, also newly surfaced as remaining), which PR #5604
was actively modifying at the time.

Added `backend/tests/rls/test_financial_ledger_extension_rls.py` (31 tests)
and extended `backend/tests/rls/conftest.py` to apply the real shipped
migration SQL for these three tables (verbatim for 59/286; `subscription_payments`
extracted up to its own backfill-INSERT comment, since that backfill reads
`FROM driver_subscriptions`, a table outside this harness's build scope —
same "read only the section this harness needs" technique already used for
migration 340).

While writing the CASCADE-delete test for `financial_event_entries`,
discovered and fixed a pre-existing bug in the shared test harness itself
(not a finding about production): `conftest.py`'s existing `financial_events`
fixture block applied migrations 58 → 70 → 290, silently skipping 289 (the
flag-gated DELETE fix letting the 7-year DSAR purge legally delete a
`financial_events` row). Every RLS test against `financial_events` so far
only ever exercised SELECT/INSERT/UPDATE, so this gap in the harness was
invisible until this round's own test needed a real DELETE. Fixed by adding
289 to the fixture in filename-sort order; all 424 tests (393 pre-existing +
31 new) pass with it applied.

No production code changed. This PR is test-coverage-only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Grepped every `.py`/`.ts`/`.tsx` file
  repo-wide for all three table names. Every real production consumer goes
  through `db_supabase` (the service-role client, dual-import pattern):
  `services/ledger_service.py`, `services/payment_service.py`,
  `utils/ledger_projection.py`, `utils/reconciliation.py` for
  `financial_event_entries`/`reconciliation_discrepancies`;
  `utils/subscription_invoice.py`, `routes/admin/subscriptions.py`,
  `routes/drivers/subscriptions.py`, `routes/webhooks.py` for
  `subscription_payments`. Two comment-only mentions (`schemas.py`,
  `core/lifespan.py`) reference the double-entry ledger projection feature,
  not a real access path. No admin-dashboard/rider-app/driver-app/shared
  file references any of the three.
- The `financial_events` fixture change (adding migration 289) touches a
  table 6+ other test files in this same directory already build against
  (`test_money_and_safety_rls.py` and others). Re-ran the **full**
  `tests/rls` suite, not just the new file, specifically to catch any
  regression from this shared-fixture change — all 424 passed, including
  every pre-existing `financial_events` test.
- Nothing in this change touches `backend/tests/conftest.py` (the separate,
  mocked-Supabase tier every other backend test uses) or any production code
  path. This tier only proves RLS policy *logic* against a throwaway
  database (ACTION_ITEMS.md C108); it makes no request against the real
  Supabase project and changes no production schema.

## 5. User-experience effect

None. Backend-only, test-coverage-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/rls/conftest.py` | Applied migrations 59/286 verbatim + 151 (extracted section) for the 3 new tables; added migration 289 to the existing `financial_events` block; by-name `GRANT`s + 290-style REVOKE re-application for `financial_event_entries`/`subscription_payments`; added the view's `service_role` grant; extended `pg_cur`'s `TRUNCATE` list | Extend the harness + fix a pre-existing gap in its `financial_events` coverage |
| `backend/tests/rls/test_financial_ledger_extension_rls.py` (new) | 31 new DB-role-level RLS tests across the 3 tables | New themed slice of ACTION_ITEMS.md C49 |
| `ACTION_ITEMS.md` | New C49 status entry (this round, with the corrected 71/46/25 total and remaining-table list); new C129 entry (2 findings, not fixed) | Track progress, correct the stale total, flag the two gaps found while writing these tests |

## 7. Before / after

Not applicable — pure test-coverage addition, no existing caller, no
behavior change.

## 8. Rollback plan

`git revert` is sufficient and safe — this PR adds no migration, no
production code, no config/flag, and touches no live data.

## 9. Verification performed

- [x] Automated tests run: the full `backend/tests/rls/` suite — pointed
  `TEST_DATABASE_URL` at the local `rlstest` cluster's trust-auth socket
  (host `localhost`, port 5544, user `postgres`, no password) and ran
  `cd backend && python3 -m pytest tests/rls -c /dev/null --confcutdir=tests/rls -q`
  → **424 passed, 0 failed**, against a real local
  Postgres 16 (`rlstest` cluster, port 5544 — re-provisioned this session
  after a container restart via `pg_ctlcluster 16 rlstest start`, isolated
  from the `main` cluster's auth config per this backlog item's own standing
  constraint).
- [ ] Manual repro steps followed in staging — not applicable, no production
  code changed.
- [x] Blast-radius grep performed: see section 4 above.
- [x] Reviewed against relevant CLAUDE.md convention(s): RLS (this entire PR
  is RLS test coverage), Testing Conventions ("every auth/RLS policy, both
  allowed and denied paths"), task-decomposition (themed 3-table slice,
  matching prior rounds' sizing).
- [ ] Feature-flagged — not applicable, test-only change.
- **Review used:** `spinr-security-auditor` via the Agent tool, against the
  actual diff — independently re-derived every asserted policy/GRANT/trigger
  from the real migration SQL, empirically confirmed each denial test
  exercises the right layer (GRANT REVOKE vs RLS policy, not just "raises
  some exception"), reran the full suite itself (424/424, reproduced
  independently), and verified both C129 findings by reading migrations
  430/432/433 directly. Verdict: **safe to merge**. Two documentation-only
  nits found and fixed before this commit: the test count was stated as 32
  in two places (actual: 31 test functions, 393 pre-existing + 31 = 424 —
  fixed here and in the ACTION_ITEMS.md C49 entry), and the new C129
  entry's "migration 430 (C107, 10 tables)" dropped `disputes` from the
  count (430's own header says "10 tables... plus `disputes`" — fixed to
  match). Two minor test-completeness observations were noted but not
  acted on: no dedicated test for `financial_event_entries`' `(event_id,
  account, side)` UNIQUE constraint, and `subscription_payments`'
  UPDATE/DELETE denial is covered by the same REVOKE already proven for
  INSERT but not independently asserted — both low-value additions given
  the underlying mechanism is already exercised, not required before
  merge.

## What was NOT verified

- Not run against CI's own `postgres:15` service container — a real local
  Postgres 16 was used instead, same caveat every prior C49 round has
  carried.
- Whether migrations 59/151/286/289 have actually been applied to
  production's live `schema_migrations` table is not confirmed here — no
  production DB access from this environment.
- The C129 findings are documented and reproduced against real Postgres
  reasoning (finding 1) / a direct migration grep (finding 2), but whether
  either is worth fixing versus an accepted risk was not decided here —
  flagged for a human/future session per CLAUDE.md's escalation gate.

## Assumptions

- Migrations 287/292/293 (`financial_event_entries` follow-ups) and 186/188
  (`subscription_payments` follow-ups) were deliberately NOT applied to the
  test harness: grepped each for `REVOKE`/`GRANT`/`POLICY` statements first —
  287/293 only touch `EXECUTE` grants on RPC functions outside this table's
  RLS surface, and 186/188 are purely additive columns with zero grant/policy
  effect. Same "only the migration that actually defines the policies"
  precedent already established for `safety_incidents`' 280/315 in an
  earlier round.
- A real local Postgres 16 (this session's `rlstest` cluster) is
  representative of CI's `postgres:15` service container for RLS-policy
  purposes — same assumption every prior C49 round has made.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — there is no
  behavior change at all, this is test-coverage-only

## 11. Merge conflict & debug log

Three merge commits landed on this branch after the original PR content
above was written, per CLAUDE.md's CI-red escalation gate (§8) — none
change anything described in sections 1–10; they only keep the branch
current and get `backend-test` green:

- `ddb880807` / `f242c7a4f` — routine `git merge origin/main`, no conflicts
  (main only moved 5 commits ahead at each point, none touching this PR's
  4 files).
- `9ab557e7c` — merged the validated fix branch `fix/backend-test-5614-fallout`
  (PR #5634) in, per gate 8: `backend-test` was showing 14 pre-existing
  failures caused by PR #5614's production changes, unrelated to this PR's
  own diff (this PR's own new RLS suite passed 424/424 in the same CI run).
  #5634 already fixes 12 of the 14 and was validated green in real CI
  before being ported here, rather than waiting for it to merge to `main`
  first.
  - **Files conflicted:** `ACTION_ITEMS.md` only.
  - **Decision rule:** merged manually — kept both sides' entries (this
    PR's `C129`, the fallout PR's `C130`). They are independent, non-
    overlapping backlog entries with no shared text; nothing was dropped.
  - **Why:** both are real, distinct tracked findings from the same day's
    work; the conflict was purely positional (both inserted after the same
    preceding entry), not a competing edit to the same content.

The 2 remaining `backend-test` failures after this merge
(`test_settings_loader_last_known.py`'s two `TestFailedReadDoesNotClobber`
tests) are a separate, order-dependent test-isolation bug — not fixed here,
tracked as [issue #5636](https://github.com/srikumarimuddana-lab/spinrvm/issues/5636)
(CR-2026-091 / ACTION_ITEMS.md C130).
