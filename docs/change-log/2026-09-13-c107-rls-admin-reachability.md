# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_01Aq7xVA8zptrqFwExvqVrwN`) |
| Surface(s) | backend (docs/analysis only — no application code changed) |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `mvapps/dreamy-faraday-2wz914-c107-rls-reachability` |
| Related issue or gap ID | ACTION_ITEMS.md C107 (closed), C108 (opened) |

## 1. Issue / gap identified

`ACTION_ITEMS.md` C107 flagged that migration 142/416's admin-read RLS policy
on 10 corporate/financial tables (`role IN ('admin','super_admin')` checked
against `users.role`) might be permanently unreachable, because migration
256 added a CHECK constraint blocking `users.role` from ever holding those
values again for any admin provisioned under the current `admin_staff`
identity model. Two open actions: (1) check production for a surviving
legacy `role='admin'` row, (2) decide whether to switch the policy to check
`admin_staff` instead, or document `users.role` as legacy/dead.

## 2. Root cause

Investigated directly against production rather than continuing to reason
from code alone. Two findings:

1. Exactly one `users` row does have `role='admin'` (id
   `71ba3eea-287f-41d8-8e48-9d794ea531e0`, created 2026-02-14) — the "single
   historical `make_admin.py` target" C107 predicted. It has **no
   corresponding `auth.users` row**, so it can never obtain a real
   Supabase-issued session and can never exercise the RLS policy in
   question, regardless of migration 256.
2. **`auth.users` has zero rows at all** — checked against all 1,955
   `public.users` rows, none has an `auth.users` counterpart. This app has
   never issued a real Supabase Auth session to any rider, driver, or
   admin. The custom JWT scheme (`JWT_SECRET`-signed, verified by backend
   dependency functions) is the only auth path that has ever existed here;
   Supabase Auth is provisioned by the platform but unused.

Finding (2) means C107's question — "can an admin ever present a JWT that
satisfies this check" — generalizes far past the 10 tables and the admin
role specifically: **no `authenticated`-role session of any kind can ever
reach PostgREST for this app**, so every `auth.uid() = ...`-shaped RLS
policy in the schema is equally dormant today. Filed as C108, left open
(documentation/architecture-clarity question, not a mechanical fix).

## 3. Fix / remediation

No code or policy change. C107's own proposed resolution (Option B —
document the `users.role` check as legacy/dead rather than migrating it to
check `admin_staff`) is confirmed correct by direct evidence: switching
which table the `EXISTS` subquery reads would not create any real
reachability today, because the blocker isn't which identity table is
checked — it's that no `authenticated`-role session exists for anyone,
admin or otherwise.

**Alternative considered:** migrate all 10 tables' admin-read policies to
check `admin_staff` instead of `users.role` (C107's Option A). **Rejected**
because it would be a real migration touching a live-tested auth surface
(10 tables, some money-adjacent) for zero actual reachability gain — the
`admin_staff` table has exactly the same problem `users.role` does: no
`authenticated`-role session with a matching `sub` can ever exist while
`auth.users` stays empty. Making a change with no functional effect, on a
surface CLAUDE.md flags for extra caution, fails the "prefer the smallest
diff that solves the problem" principle for no benefit.

C107 closed. C108 opened for the broader, schema-wide version of the same
fact — recommends (not implemented here) rewording `backend/tests/rls/`'s
docstrings and CLAUDE.md's RLS testing-conventions paragraph to say
"policy-logic coverage, not live-traffic coverage," since zero `auth.users`
rows exist to make it the latter.

## 4. Risk & impact on existing functionality

- **Blast radius: zero.** No code, migration, or policy changed. Only
  `ACTION_ITEMS.md` (C107 status update, C108 new entry) and this log.
- **Other readers/writers of the same tables:** unaffected — nothing about
  the 10 tables' actual grants or policies changed.
- **Verification method itself carries no risk to production:** every query
  run was read-only (`SELECT count(*)`, existence/boolean checks). No
  `INSERT`/`UPDATE`/`DELETE`/`apply_migration` calls were made against the
  production project despite the connector currently holding broader
  read/write access (see `.claude/context/connector-scoping.md`'s Supabase
  row) — used at the narrowest scope the task needed, not the broadest the
  connector allows.
- **No PII exposed or persisted:** queries returned only a row count, a
  boolean `has_auth_account` flag, and one internal `user_id` UUID (no
  email, phone, or name was selected or is reproduced anywhere in
  `ACTION_ITEMS.md` or this log) — consistent with this repo's own
  logging convention of using `user_id` rather than PII as an identifier.

## 5. User-experience effect

None. Zero rider/driver/admin-visible surface — this is backend RLS
architecture analysis, not a runtime behavior change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `ACTION_ITEMS.md` | C107 marked CLOSED with verified production findings; new C108 entry opened for the broader `auth.users`-empty finding | Documents the investigation's conclusion and the follow-on discovery, per this repo's convention |
| `docs/change-log/2026-09-13-c107-rls-admin-reachability.md` | This file, added | CLAUDE.md's mandatory Change Impact Log for anything touching a live-tested (auth) surface, even a docs-only resolution |

## 7. Before / after

Not applicable — no code diff. The "before" was an open question in
`ACTION_ITEMS.md`; the "after" is that question answered with production
evidence and closed, plus a new, narrower-scoped follow-up question (C108)
opened.

## 8. Rollback plan

`git-revert-safe` — reverting this commit restores C107 to OPEN and removes
the C108 entry. No functional system state depends on either doc change.

## 9. Verification performed

- [x] Queried the real production Supabase project (`spinrmobileapp`,
      `soavhtdhefowwvforzwb`, confirmed as PRODUCTION per
      `.claude/context/connector-scoping.md`) directly, read-only:
      - `SELECT role, count(*) FROM users WHERE role IN (...) GROUP BY role`
        → exactly one `role='admin'` row.
      - A join checking that row's `id` against `auth.users` → no match
        (`has_auth_account: false`).
      - `SELECT count(*) FROM users`, `count(*) FROM auth.users`, and a
        full join-count of `public.users` against `auth.users` → 1,955 /
        0 / 0 respectively.
- [x] Read `backend/tests/rls/conftest.py`'s `as_role()` helper to confirm
      it simulates PostgREST's post-auth role-switch via `SET ROLE` +
      `set_config('request.jwt.claims', ...)` rather than requiring (or
      proving) a real Supabase Auth session — the basis for C108's claim
      that the RLS test suite pins policy logic, not live reachability.
- [x] Read `backend/migrations/142_fix_rls_financial_tables.sql`,
      `256_users_role_reject_admin_values.sql`,
      `416_corporate_accounts_rls_super_admin_fix.sql`, and
      `backend/dependencies/__init__.py`'s `_verify_admin_payload` in full
      to confirm the admin-auth model description in C107/C108 is accurate,
      not assumed from the ACTION_ITEMS.md summary alone.
- [ ] Not verified: whether a separate staging/pre-prod Supabase project (if
      one exists) shows the same `auth.users`-empty pattern — this session
      only had access to (and only checked) the one confirmed-production
      project.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`, no
      functional dependency.
- [x] Blast radius stated: zero code/behavior change; two documentation
      entries.
- [x] No silent behavior change — nothing changed at runtime; §5 states
      plainly there is no user-visible effect.

## What was NOT verified

- Whether any environment other than the one confirmed-production Supabase
  project has a different `auth.users` population (see §9).
- Whether the single legacy `role='admin'` row should be cleaned up (reset
  to a real rider/driver role, or migrated to `admin_staff`) — out of scope
  for this investigation; C107's own text already noted this row "should be
  migrated to `admin_staff` or reset," and that housekeeping wasn't done
  here since it's a data change with no bearing on the reachability
  question this entry answers (the row already cannot authenticate,
  cleaning it up doesn't change any RLS outcome).
- The suggested documentation reword for `backend/tests/rls/`'s docstrings
  and CLAUDE.md's RLS testing-conventions paragraph (C108's "suggested next
  step") — filed as future work, not implemented in this change.
