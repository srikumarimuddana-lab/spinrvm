# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session_016N2vRqybAY6LqEr8Yg7RUB) |
| Surface(s) | backend (migrations, RLS tests) |
| Domain (Sentry tag) | corporate |
| PR / commit link | fix/corporate-accounts-super-admin-rls (see PR) |
| Related issue or gap ID | Found by PR #5305's (not yet merged, `test/c49-rls-coverage-expansion`) new `test_corporate_billing_rls.py` |

## 1. Issue / gap identified

`corporate_accounts`'s admin RLS policy (migration 17) still has the exact
shape migration 142 already fixed on 9 sibling corporate tables in one round
of hardening: `FOR ALL TO authenticated`, no `WITH CHECK`, checking only
`users.role = 'admin'`. `corporate_accounts` predates those 9 tables (created
by migration 17, not migration 27) and was missed from migration 142's sweep.

## 2. Root cause

Migration 142's header comment names its own target precisely: "migration 27
dynamically created 'Admin full access `<table>`' FOR ALL TO authenticated on
**nine** tables (with only `role = 'admin'`, accidentally excluding
`super_admin`)." `corporate_accounts`'s policy was never created by migration
27 — it's a standalone `CREATE POLICY` in migration 17 — so migration 142's
`FOREACH t IN ARRAY [...]` loop never iterated over it. No later migration
touched it (grepped every migration for `corporate_accounts` + `POLICY`:
migrations 17, 27, 08 reference the table, only 17 defines its RLS policy).
This is a straightforward omission, not a deliberate design choice: nothing
distinguishes `corporate_accounts` from its 9 siblings in a way that would
justify a different policy shape.

**Which direction the bug runs (both, simultaneously):**
- **Too restrictive**: a `super_admin`-role authenticated JWT is denied
  entirely (`SELECT` included) where an `admin`-role JWT is allowed — the
  literal symptom PR #5305's test caught.
- **Too permissive**: an `admin`-role authenticated JWT still has
  unrestricted PostgREST `INSERT`/`UPDATE`/`DELETE` with no `WITH CHECK`
  clause on `corporate_accounts` — the same P0-class gap migration 142's own
  title ("RLS lockdown … Any authenticated JWT could INSERT/UPDATE/DELETE
  these rows via PostgREST, bypassing every backend guard") closed on the 9
  siblings but never reached this table.

Fixing only the role check and leaving `FOR ALL`/no-`WITH CHECK` in place
would leave `corporate_accounts` as the *only* corporate table still carrying
the original P0 shape, which does not actually mirror "the 9 sibling tables'
now-correct behavior" as intended — so this migration applies migration 142's
full fix pattern, not just the role-list edit.

## 3. Fix / remediation

New migration `backend/migrations/416_corporate_accounts_rls_super_admin_fix.sql`,
applying migration 142's exact section-2 pattern to `corporate_accounts`
only:
- `DROP POLICY "Admin full access corporate_accounts"` (the migration-17
  `FOR ALL` policy).
- `CREATE POLICY "Admin read corporate_accounts" ... FOR SELECT ... USING
  (... users.role IN ('admin', 'super_admin'))` — same naming convention
  (`"Admin read <table>"`) and same role-check idiom migration 142 used.
- `REVOKE ALL ... FROM anon`, `REVOKE INSERT, UPDATE, DELETE, TRUNCATE ...
  FROM authenticated`, `GRANT SELECT ... TO authenticated` — same
  grant-layer lockdown migration 142 applied per table.

Does not touch any of the 9 tables migration 142 already fixed, and does not
touch PR #5305's files.

## 4. Risk & impact on existing functionality

**Blast-radius grep performed**: every backend reference to
`corporate_accounts` (`grep -rln corporate_accounts backend --include=*.py`,
60 files) reads/writes it through `repositories/corporate_repo.py` /
`db_supabase.py` / `routes/corporate_*.py` / `services/corporate_*.py` — all
of which go through the single Supabase client this repo ever constructs,
`backend/supabase_client.py`, which is **always** built with
`SUPABASE_SERVICE_ROLE_KEY` (verified: it is the only `create_client(...)`
call site in `backend/`, and `service_role` carries `BYPASSRLS`). Also
grepped `admin-dashboard/`, `rider-app/`, `driver-app/` for `createClient`/
`supabase-js` usage: matches are dependency-manifest only (`package.json`,
`yarn.lock`, `tsconfig.json`), no source file constructs its own Supabase
client — `admin-dashboard/.../corporate-accounts/page.tsx`'s
`corporate_accounts` string is an admin-module permission key
(`useRequireModule("corporate_accounts")`), not a table query.

**Conclusion — real production impact: none, today.** No code path in this
repo (backend, rider-app, driver-app, or admin-dashboard) ever authenticates
to Supabase/PostgREST as `anon` or `authenticated` to reach
`corporate_accounts`; every reader/writer goes through the service-role
client, which bypasses RLS by Postgres design regardless of what this policy
says. This matches migration 142's own stated rationale for its 9 tables
("apps never talk to PostgREST directly … policies exist for convention
compliance and future direct-read tooling, not because any current code path
needs them") — the same conclusion applies here, unchanged by this fix.

**Related but out-of-scope finding, surfaced for the record:** migration 256
(`chk_users_role_not_admin`, added after 142) added a `NOT VALID` CHECK
constraint that blocks `users.role` from ever being set to `'admin'` /
`'super_admin'` (or `'operations'`/`'support'`/`'finance'`/`'custom'`) again
— its own comment states plainly "Real admin identities live in
`admin_staff`, NOT in `users.role`." Because it is `NOT VALID`, pre-existing
rows are not revalidated, but no *new* row can carry an admin-class
`users.role` value going forward. This means the entire
`users.role IN ('admin', 'super_admin')` RLS idiom — the one migration 142
established and this migration mirrors — may already be unreachable for any
admin/super_admin provisioned after migration 256 shipped, on all 10 tables
(the 9 siblings plus this one), not just this one. This is **not** introduced
or worsened by this change (it predates this fix and equally affects the 9
already-fixed tables), and it does not change the "no live impact" conclusion
above (service-role bypass makes the point moot either way for current
traffic) — but it is a real, unverified-here question about whether this
policy shape is even the right one anymore, independent of this migration's
narrow scope. Flagged rather than silently left implied; not fixed in this
PR (would require redesigning the shared idiom across 10 tables, touching
migration history other PRs — including #5305 — may also depend on, and is
a materially different, larger change than this parity fix).

**Other readers/writers of the same policy/grant surface**: none found —
`corporate_accounts`'s admin policy and grants are not referenced from any
other migration or application code path (grepped the full migration
history and `backend/` source for the policy name and for
`GRANT`/`REVOKE ... corporate_accounts`).

**Blast radius: isolated.** Single table, single migration, no application
code changed, no other migration touched.

## 5. User-experience effect

None. No rider, driver, corporate-admin, or internal-admin-facing behavior
changes — the backend never relied on this RLS path (see §4), so nothing an
already-open session observes changes, mid-session or otherwise.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/416_corporate_accounts_rls_super_admin_fix.sql` | New migration: drops `corporate_accounts`'s migration-17 `FOR ALL`/no-`WITH CHECK`/`admin`-only policy, replaces it with a `SELECT`-only policy checking `role IN ('admin', 'super_admin')`, and locks down the table-level grants (`REVOKE` from `anon`/write-from-`authenticated`, `GRANT SELECT` to `authenticated`) | Mirror migration 142's already-applied fix, scoped to the one corporate table it missed |
| `backend/tests/rls/test_corporate_accounts_super_admin_fix.py` | New DB-role-level RLS test file: builds `corporate_accounts` (migration 05) + the pre-fix policy (migration 17) + the fix (migration 416) in its own module fixture, then asserts `super_admin` can now `SELECT` (the fix), `admin` still can (no regression), `rider`/`anon` still cannot, no authenticated role can `INSERT`, and `service_role` still bypasses RLS | Regression pin proving the fix, isolated from PR #5305's in-flight `test_corporate_billing_rls.py` |

## 7. Before / after

```sql
-- Before (migration 17, still live today)
CREATE POLICY "Admin full access corporate_accounts"
ON corporate_accounts FOR ALL
TO authenticated
USING (
    EXISTS (
        SELECT 1 FROM users
        WHERE users.id = auth.uid()::text
          AND users.role = 'admin'
    )
);
-- (no REVOKE/GRANT statements ever applied to this table — still on
-- Supabase's default broad anon/authenticated grants)
```

```sql
-- After (migration 416)
CREATE POLICY "Admin read corporate_accounts"
    ON corporate_accounts FOR SELECT
    TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM users
            WHERE users.id = auth.uid()::text
              AND users.role IN ('admin', 'super_admin')
        )
    );

REVOKE ALL ON corporate_accounts FROM anon;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON corporate_accounts FROM authenticated;
GRANT SELECT ON corporate_accounts TO authenticated;
```

## 8. Rollback plan

Additive-not-destructive — a follow-up migration restores migration 17's
original policy and grants verbatim (no data was touched or lost; this is
metadata-only):

```sql
DROP POLICY IF EXISTS "Admin read corporate_accounts" ON corporate_accounts;
GRANT INSERT, UPDATE, DELETE, TRUNCATE ON corporate_accounts TO authenticated;
GRANT ALL ON corporate_accounts TO anon;
CREATE POLICY "Admin full access corporate_accounts"
    ON corporate_accounts FOR ALL TO authenticated
    USING (EXISTS (SELECT 1 FROM users WHERE users.id = auth.uid()::text
                     AND users.role = 'admin'));
```

No feature flag applies (this is a DB policy, not app-code-gated behavior);
no live data (Stripe charges, wallet deltas, ride state) is touched by this
change, so a straightforward follow-up migration is a complete rollback —
this is also stated as the top-of-file comment in migration 416 itself, per
`backend/migrations/CLAUDE.md`'s "always reversible on paper" rule.

## 9. Verification performed

- [x] `python -m py_compile backend/tests/rls/test_corporate_accounts_super_admin_fix.py` — passes.
- [x] `ruff check backend/tests/rls/test_corporate_accounts_super_admin_fix.py` — passes, no findings.
- [x] `pytest tests/rls/test_corporate_accounts_super_admin_fix.py -c /dev/null --confcutdir=tests/rls --collect-only` — collects all 6 tests cleanly, same shape/imports as the existing RLS test files in this directory.
- [x] Confirmed CI *will* give this a genuine real-Postgres run even though this sandbox couldn't: `.github/workflows/ci.yml`'s dedicated "Run RLS role-level tests (real Postgres)" step runs `TEST_DATABASE_URL=... pytest tests/rls -c /dev/null --confcutdir=tests/rls -v` against a real `postgres:15` service container on every PR — this new file is picked up by that same `tests/rls` invocation with no workflow change needed.
- [x] Blast-radius grep performed — see §4 (every backend `corporate_accounts` reader/writer traced to the service-role client; every frontend surface checked for a direct Supabase client; no other migration/code references this policy by name).
- [x] Reviewed against `backend/migrations/CLAUDE.md` (naming, append-only, RLS pattern) and `CLAUDE.md`'s RLS/Query-filter/data-layer conventions.
- [x] Self-applied the `spinr-migration-reviewer` and `spinr-security-auditor` checklists against the diff (no dedicated Agent-launch tool was available in this sandbox to invoke them as actual subagents — see PR description for the full self-review output and that caveat). No blockers found by either checklist.
- [ ] Feature-flagged: not applicable — RLS policy changes have no app-level flag mechanism in this codebase, and §4 establishes there is no live-app-visible behavior to gate.

## 10. What was NOT verified

- **No real-Postgres run performed by this session.** This sandbox has no
  reachable Postgres with `CREATE DATABASE`/`CREATE ROLE` rights for
  `backend/tests/rls/` (attempting
  `pytest tests/rls/... -c /dev/null --confcutdir=tests/rls` without
  `TEST_DATABASE_URL`/`DATABASE_URL` set produces a real `psycopg2
  .OperationalError: ... role "root" does not exist` from `psycopg2.connect
  (None)` falling back to local peer auth as the sandbox's OS user — expected
  and not worked around, per instructions). The migration's SQL has not
  executed against a real Postgres in this session; only `py_compile` +
  `ruff` + `--collect-only` verification was possible here. **This PR's own
  CI will run it for real regardless** — see §9: `ci.yml` runs `tests/rls`
  against a real `postgres:15` service container on every PR, so the actual
  merge-gating signal for this test file will be a genuine real-Postgres
  result, just not one this session could produce itself.
  - While confirming the above, reproduced the same local connection error
    against an already-merged sibling file
    (`tests/rls/test_money_and_safety_rls.py`), which independently confirms
    a quirk `ci.yml` itself already documents in its own comments (search
    "does NOT propagate to sibling test modules" in the RLS step's
    surrounding comment block): `backend/tests/rls/conftest.py`'s
    module-level `pytestmark = pytest.mark.skipif(...)` does not actually
    gate other test modules' fixtures from attempting a real connection when
    no test DB is configured (`pytestmark` only marks tests within the
    module that defines it) — it only *reads* as a clean self-skip when
    `TEST_DATABASE_URL`/`DATABASE_URL` happen to be genuinely unset **and**
    unreachable-by-default, which isn't this sandbox's situation. Not a new
    finding, not this PR's to fix, and irrelevant to CI (which always sets
    `TEST_DATABASE_URL` for real) — noted only so "collect-only, not a real
    run" isn't mistaken for a clean self-skip in this write-up.
- **Production `users` table state for the migration-256 caveat in §4** —
  whether any legacy `users.role = 'admin'`/`'super_admin'` rows still exist
  (pre-dating migration 256's `NOT VALID` constraint) was not checked; this
  session has no production DB access. It does not change this PR's
  conclusion (service-role bypass makes the RLS path unreachable by current
  traffic regardless), but it does affect whether the fixed policy is
  reachable at all in the hypothetical direct-PostgREST scenario it exists
  for.
- **No staging/live Supabase check** — this is a schema-only migration
  applied through the normal migration pipeline once merged, not applied
  directly by this session (per instructions).
