# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_01Aq7xVA8zptrqFwExvqVrwN`) |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `mvapps/dreamy-faraday-2wz914-c49-rls-coverage` (PR #5343, second round on the same branch) |
| Related issue or gap ID | ACTION_ITEMS.md C49 (progressed further, not closed) |

## 1. Issue / gap identified

Four corporate tables from migration 27's original nine-table group —
`corporate_policies`, `corporate_allowed_domains`, `corporate_policy_evaluations`,
`ride_payment_sources` — were already created and access-granted in this
repo's real-Postgres RLS test harness, but had zero DB-role-level test
coverage. `test_corporate_billing_rls.py`'s own module docstring explicitly
named these four and said they were "left for a future round."

## 2. Root cause

Not a bug — a coverage gap, and an explicitly tracked one (the prior
round's own docstring named exactly this scope). Picked up now as the
natural next slice of the ongoing C49 backlog item.

## 3. Fix / remediation

Three of the four (`corporate_policies`, `corporate_allowed_domains`,
`corporate_policy_evaluations`) share the exact same `id`-keyed,
admin/super_admin-read-only, no-write shape as five tables this file
already tested — added them to the existing `_ADMIN_ONLY_MONEY_TABLES`
parametrized tuple, which reuses all 8 existing parametrized test
functions with zero new test-function code, just new seed helpers
(`_seed_policy`, `_seed_allowed_domain`, `_seed_policy_evaluation`) and
`_seed_chain`/`_INSERT_FN` entries.

`ride_payment_sources` has the identical policy shape but a different
primary key column (`ride_id`, not `id` — migration 27's own choice), so
it can't share the generic `SELECT/UPDATE/DELETE ... WHERE id = %s` SQL
the parametrized tests use. Gave it its own dedicated, non-parametrized
9-test block mirroring the same 9 scenarios (admin/super_admin select,
non-admin/anon denied, insert/update/delete denied, service_role bypass).

Also extended `conftest.py`'s `pg_cur` fixture TRUNCATE list to include
all 4 new tables. No new GRANT statement was needed — the existing
corporate-tables GRANT (from the prior round) already named all 9 of
migration 27's tables, these 4 included.

**Kept on the same branch/PR as the already-open round-1 work** (PR #5343)
rather than opening a new PR. **Alternative considered:** a separate PR,
matching the pattern used for earlier, unrelated C49 rounds. **Rejected**
because both rounds touch the exact same `conftest.py` insertion point
(the `pg_cur` TRUNCATE list and the corporate-tables section) — a second
open PR would have guaranteed a merge conflict against the still-open
first one. Stacking related, same-backlog-item work as a second commit on
one branch avoided that entirely, per the standing preference to avoid
overlapping merge conflicts between parallel work.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, test-only.** No production code changed. Two
  files: `backend/tests/rls/conftest.py` (TRUNCATE list extended) and
  `backend/tests/rls/test_corporate_billing_rls.py` (extended).
- **Adversarial review (`spinr-money-auditor`, CLAUDE.md gate #10)
  performed before commit** — mandatory since corporate billing tables
  move real money via `corporate_wallet_apply_delta`, per this test file's
  own docstring:
  - Independently re-derived migration 142's policy treatment of all 4
    tables directly from its `DO $$ ... FOREACH t IN ARRAY [...]` loop —
    confirmed all 4 get the identical DROP-FOR-ALL/CREATE-SELECT-admin-or-
    super_admin-read/REVOKE-ALL-anon/REVOKE-write-authenticated treatment,
    and confirmed none of the 4 gets the "member read own" policy the
    other three tested tables have.
  - Found one real-but-harmless subtlety: `corporate_policies.company_id`
    is `UNIQUE`, and the insert-denial test (`test_authenticated_cannot_insert`/
    `test_anon_cannot_insert`, parametrized) reused an already-seeded
    `company_id`. This would raise `UniqueViolation` instead of the
    intended `InsufficientPrivilege` *only if* Postgres evaluated table
    constraints before table-level privilege grants — it does the
    opposite (privilege check happens at executor-start, before any row is
    processed), confirmed by direct reproduction outside the test suite.
    The test was passing for the correct reason, not by accident. Fixed
    anyway, as a diagnostic-clarity improvement: `_INSERT_FN["corporate_policies"]`
    now seeds a fresh company first, so a real future regression (e.g. someone
    accidentally re-granting INSERT to `authenticated`) fails with an
    unambiguous "did not raise" or a clean grant-related error, not a
    `UniqueViolation` that could read as "the test itself is broken."
  - Cross-checked every real production write path to all 4 tables
    (`repositories/corporate_repo.py`, `services/payment_service.py`,
    `routes/corporate_company.py`, `routes/corporate_rider.py`,
    `services/corporate_membership_service.py`, plus a full frontend grep)
    — every one goes through the single service-role Supabase client; zero
    hits on any authenticated-role or anon-key path. The tests' "no
    authenticated/anon write in production" premise matches reality
    exactly; tightening this policy further would not break any live code
    path.
  - **Verdict: safe to merge, zero bugs found in the diff.**
- **Other consumers checked:** grepped every file in `backend/tests/rls/`
  for these 4 table names — only `conftest.py` and
  `test_corporate_billing_rls.py` reference them. `_seed_chain` (called by
  ~18 pre-existing, unrelated test functions in this file) now also seeds
  one row each in `corporate_policies`/`corporate_allowed_domains`/
  `corporate_policy_evaluations` per call — checked every existing
  assertion in the file and confirmed none does a bare row-count check;
  all filter by the specific seeded ID(s) returned in `ids`, so the extra
  rows cannot break any pre-existing test.

## 5. User-experience effect

None. Test-only change; no production code, no deploy artifact, no
behavior change to any rider/driver/admin/corporate-facing flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/rls/conftest.py` | Added the 4 new tables to the `pg_cur` TRUNCATE list; module docstring's coverage-scope note updated | Per-test isolation for the 4 newly-tested tables |
| `backend/tests/rls/test_corporate_billing_rls.py` | Extended `_ADMIN_ONLY_MONEY_TABLES` (3 tables) + new dedicated `ride_payment_sources` block (9 tests); new seed helpers; `_seed_chain`/`_INSERT_FN` extended; module docstring updated | DB-role-level RLS coverage for the 4 tables |
| `ACTION_ITEMS.md` | C49 status updated with this round | Keep the backlog record accurate |

## 7. Before / after

Not applicable — pure test addition, no existing behavior changed.

## 8. Rollback plan

`git-revert-safe` — a plain revert removes this round's additions. No
data, no migration, no config involved either direction.

## 9. Verification performed

- [x] Automated tests run: `test_corporate_billing_rls.py` standalone (92
      passed) and the full `backend/tests/rls/` suite (266 passed, 0
      failed) against a real local Postgres 16 instance, both before and
      after the diagnostic-clarity fix from adversarial review.
- [x] Blast-radius grep performed: every RLS test file for a reference to
      the 4 new tables; every backend/frontend write path to all 4 tables.
- [x] Reviewed against relevant CLAUDE.md conventions: corporate billing
      is explicitly money-touching, so the mandatory adversarial review
      (gate #10) was run and passed before commit.
- [x] Adversarial review performed and passed — see §4.

## What was NOT verified

- Not run against CI's own `postgres:15` container — a real local Postgres
  16 instance was used instead, same as every other round of this backlog
  item.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`, test-only.
- [x] Blast radius stated: two files, test-only, no other consumer affected.
- [x] No silent behavior change to a working flow — nothing in production
      changed; this is pure test coverage addition.
