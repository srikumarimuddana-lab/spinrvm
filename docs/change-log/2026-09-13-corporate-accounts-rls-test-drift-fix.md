# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session_016N2vRqybAY6LqEr8Yg7RUB) |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | corporate |
| Related issue or gap ID | none — CI-red investigation while merging PR #5306 |

## 1. Issue/gap identified

`backend/tests/rls/test_corporate_billing_rls.py` (added by PR #5305) and
`backend/tests/rls/test_corporate_accounts_super_admin_fix.py` (added by PR #5307)
were built independently and merged the same day, each assuming a different
schema state for `corporate_accounts`. Once both were on `main` together, the
real-Postgres RLS suite (`.github/workflows/ci.yml`'s "Run RLS role-level tests")
failed: 5 tests in `test_corporate_billing_rls.py` asserted the *pre-#5307* policy
shape (super_admin denied, admin can write), and running both files' fixtures in
one session double-applied migration 416 (each file's own fixture applied it
independently), erroring out `test_corporate_accounts_super_admin_fix.py`'s 6 tests.

## 2. Root cause

Two independent, parallel PRs each touched the same table's test coverage without
knowing about the other (the same class of collision as PR #5306 vs PR #5299 on
`maps_budget.py`, found and resolved earlier the same session):

- PR #5305's tests were written and merged assuming `corporate_accounts` still had
  migration 17's original (buggy) admin-only policy — accurate when written, since
  PR #5307 (which fixes that policy) hadn't merged yet.
- PR #5307's own new test file built its own self-contained copy of the
  `corporate_accounts` schema (migrations 05/17/416) via a `scope="module"`
  fixture, written on the accurate-at-the-time assumption that `conftest.py`'s
  shared fixture didn't yet build this table — true before PR #5305 merged its
  own extension of that shared fixture (which does build it, migrations 05/17/27).

Neither PR's own CI run could have caught this: each ran in isolation against the
schema state its own branch produced, never against the other's.

## 3. Fix/remediation

- `backend/tests/rls/conftest.py`: added migration 416's application to the
  shared schema-bootstrap fixture, sequenced after the fixture's baseline
  `GRANT ... TO anon, authenticated, service_role` block (same reasoning the
  fixture already documents for why migration 142's fix is sequenced there too —
  granting then narrowing, not narrowing then re-granting over it).
- `backend/tests/rls/test_corporate_billing_rls.py`: updated 5 tests + the module
  docstring to assert the corrected, post-416 policy (super_admin can now select;
  admin/non-admin/anon can no longer write via RLS at all, since 416 revokes
  INSERT/UPDATE/DELETE/TRUNCATE from `authenticated` and ALL from `anon`).
- `backend/tests/rls/test_corporate_accounts_super_admin_fix.py`: removed its own
  now-redundant, now-conflicting module-scoped fixture (`_corporate_accounts_schema`)
  since the shared conftest.py fixture provides the same, single, correct schema.
  Updated the module docstring to record why.

## 4. Risk & impact on existing functionality

Test-only change — no application code touched. Blast radius is the RLS test
harness itself: grepped `backend/tests/rls/` for every other file relying on
`conftest.py`'s shared fixture or on `corporate_accounts`; none besides the two
files above reference this table. Re-ran the full `backend/tests/rls/` suite
(194 tests) against a real local Postgres 16 instance after the fix — all pass.

## 5. User-experience effect

None. No application code, only test fixtures/assertions.

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `backend/tests/rls/conftest.py` | Added migration 416's application to the shared schema fixture | The fixture built `corporate_accounts` through migration 27 but never applied 416, so every test using this shared fixture saw the pre-#5307 policy |
| `backend/tests/rls/test_corporate_billing_rls.py` | Updated 5 tests + module docstring to match the post-416 policy | Tests asserted the old, now-fixed bug as expected behavior |
| `backend/tests/rls/test_corporate_accounts_super_admin_fix.py` | Removed the file's own duplicate schema-build fixture | It double-applied migration 416 once the shared fixture also applied it |

## 7. Before/after

Before (in the shared fixture, `conftest.py`):
```python
migration_142_sql = (migrations_dir / "142_fix_rls_financial_tables.sql").read_text()
cur.execute(_extract_section(migration_142_sql, "-- 2. Corporate financial tables:", "-- 3. PIPEDA data minimization:"))
# (migration 416 never applied)
```

After:
```python
migration_142_sql = (migrations_dir / "142_fix_rls_financial_tables.sql").read_text()
cur.execute(_extract_section(migration_142_sql, "-- 2. Corporate financial tables:", "-- 3. PIPEDA data minimization:"))

cur.execute((migrations_dir / "416_corporate_accounts_rls_super_admin_fix.sql").read_text())
```

## 8. Rollback plan

`git revert` — test-only change, no data or schema impact to unwind.

## 9. Verification performed

Ran `pytest backend/tests/rls -c /dev/null --confcutdir=tests/rls` against a real
local Postgres 16 instance (not mocked, not collect-only) both before and after
the fix: 6 failures / 6 errors before, **194 passed / 0 failed after**. `ruff
check` + `ruff format --check` clean on all 3 changed files.

## 10. What was NOT verified

This was verified against a local Postgres 16 instance in this session's own
sandbox, not against CI's `postgres:15` service container specifically — the SQL
involved (RLS policies, GRANT/REVOKE, standard DML) has no version-specific
behavior between 15 and 16 relevant here, but CI's own run is the authoritative
confirmation.
