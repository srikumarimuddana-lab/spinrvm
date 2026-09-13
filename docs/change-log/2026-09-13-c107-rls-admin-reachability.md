# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_01Aq7xVA8zptrqFwExvqVrwN`) |
| Surface(s) | backend — analysis + one production data correction (no application code) |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `mvapps/dreamy-faraday-2wz914-c107-rls-reachability` |
| Related issue or gap ID | ACTION_ITEMS.md C107 (closed), C108 (opened, corrected) |

**Note:** this investigation also surfaced a real, live P0 vulnerability (a
WebSocket admin-auth bypass), filed and fixed as C109 with its own separate
Change Impact Log: `docs/change-log/2026-09-13-c109-websocket-admin-bypass.md`.
That is a code fix on a different file; this log covers only the RLS
reachability analysis and the one production data correction it led to.

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

No code or policy change for the RLS reachability question itself. C107's
own proposed resolution (Option B — document the `users.role` check as
legacy/dead rather than migrating it to check `admin_staff`) is confirmed
correct by direct evidence: switching which table the `EXISTS` subquery
reads would not create any real reachability today, because the blocker
isn't which identity table is checked — it's that no `authenticated`-role
session exists for anyone, admin or otherwise.

One production **data** correction was made as defense-in-depth, prompted
by `spinr-security-auditor`'s adversarial review of this closure (see §3a):
the legacy `role='admin'` row was reset to `role='rider'`, and
`chk_users_role_not_admin` (previously `NOT VALID` since migration 256) was
validated for real via `ALTER TABLE users VALIDATE CONSTRAINT
chk_users_role_not_admin`, converting a one-time manual check into a
standing, Postgres-enforced guarantee.

### 3a. Correction from adversarial review

`spinr-security-auditor`'s review of this closure (CLAUDE.md gate #10)
found two things this entry originally got wrong or left too casually
deferred:

1. **C108's reachability mechanism was wrong**, though its conclusion
   survived on different grounds — see the correction now recorded directly
   in C108's own entry (`auth.users`-empty is corroborating evidence, not
   the operative guarantee; the real guarantee is no anon-key Supabase
   client existing in shipped code plus a separate, non-Supabase-trusted
   JWT signing secret).
2. **The legacy `role='admin'` row deserved more urgency than "not done
   here."** This entry's first draft (see §"What was NOT verified" below,
   left in place as the historical record) reasoned that cleanup didn't
   matter because the row already couldn't authenticate via Supabase
   Auth/RLS. That reasoning didn't consider the app's *actual* login system
   (OTP/Firebase, not Supabase Auth) — and the review found that
   `backend/routes/websocket.py`'s admin gate trusted the raw `users.role`
   column without verifying the caller ever went through real admin
   authentication, meaning this exact row *was* a live risk through a
   completely different code path than the one this entry was checking.
   Fixed as C109 (separate Change Impact Log). The row is cleaned up here
   as defense-in-depth now that the code path is also fixed — belt and
   suspenders, not a substitute for either fix alone.

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

- **Blast radius: one row, one constraint validation, no code/migration/policy
  change.** `ACTION_ITEMS.md` (C107/C108 updates) and this log, plus a
  single-row `UPDATE` on `public.users` and one `ALTER TABLE ... VALIDATE
  CONSTRAINT` in production.
- **Other readers/writers of the affected row:** confirmed via direct query
  before touching it — `is_driver=false`, no `drivers` row, no `admin_staff`
  row, zero rides as rider or driver. Never a functioning account on any
  code path; resetting its `role` changes nothing any current feature reads.
- **`VALIDATE CONSTRAINT` risk:** this only re-checks existing rows against
  a constraint already enforced (as `NOT VALID`) on every new write since
  migration 256 — it cannot reject or block any current traffic, only
  confirm no existing row violates it. Ran only after confirming (via
  `count(*)`) zero rows held any of the six admin-shaped role strings.
- **Verification queries carried no risk:** every read was `SELECT
  count(*)` / existence-check only. The one write (`UPDATE ... WHERE id =
  '<uuid>' AND role = 'admin'`) was scoped to the exact row identified,
  confirmed idempotent-safe by the `AND role = 'admin'` guard, and the
  connector's existing broader read/write access (see
  `.claude/context/connector-scoping.md`'s Supabase row) was used at the
  narrowest scope this task needed — one targeted row, not a bulk operation.
- **No PII exposed or persisted:** queries returned only a row count, a
  boolean `has_auth_account` flag, an `is_driver` boolean, ride-count
  integers, and one internal `user_id` UUID (no email, phone, or name was
  selected or is reproduced anywhere in `ACTION_ITEMS.md` or this log) —
  consistent with this repo's own logging convention of using `user_id`
  rather than PII as an identifier.

## 5. User-experience effect

None. Zero rider/driver/admin-visible surface — this is backend RLS
architecture analysis, not a runtime behavior change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `ACTION_ITEMS.md` | C107 marked CLOSED with verified production findings + follow-up cleanup note; C108 opened, then corrected in place per adversarial review | Documents the investigation's conclusion and the follow-on discoveries, per this repo's convention |
| `docs/change-log/2026-09-13-c107-rls-admin-reachability.md` | This file, added then revised after adversarial review | CLAUDE.md's mandatory Change Impact Log for anything touching a live-tested (auth) surface |
| *(production, not a repo file)* `public.users` row `71ba3eea-287f-41d8-8e48-9d794ea531e0` | `role`: `'admin'` → `'rider'` | Defense-in-depth cleanup of the one confirmed-dead-end legacy row, per C107's own original suggestion and the security review's follow-up |
| *(production, not a repo file)* `public.users` table constraint `chk_users_role_not_admin` | `NOT VALID` → validated | Converts a one-time manual "no violating rows" check into a standing, Postgres-enforced guarantee |

## 7. Before / after

No repo code diff for the RLS reachability question itself (see
`docs/change-log/2026-09-13-c109-websocket-admin-bypass.md` for the actual
code fix this investigation led to). The production data change:

```sql
-- Before
-- users.id = '71ba3eea-287f-41d8-8e48-9d794ea531e0', role = 'admin'
-- chk_users_role_not_admin: NOT VALID (enforced on new writes only)
```

```sql
-- After
UPDATE users SET role = 'rider' WHERE id = '71ba3eea-287f-41d8-8e48-9d794ea531e0' AND role = 'admin';
ALTER TABLE users VALIDATE CONSTRAINT chk_users_role_not_admin;
-- chk_users_role_not_admin: validated (Postgres-enforced against all existing rows too)
```

## 8. Rollback plan

`revert-plus-data-cleanup` for the full change: the `ACTION_ITEMS.md`/log
edits are `git-revert-safe` on their own, but the production `UPDATE` and
`VALIDATE CONSTRAINT` already applied to live data are not undone by a code
revert. Rollback for those, if ever needed: `UPDATE users SET role =
'admin' WHERE id = '71ba3eea-287f-41d8-8e48-9d794ea531e0'` restores the
prior value (the row's other fields were not touched); a validated
`CHECK` constraint has no meaningful "un-validate" — dropping and
re-adding it `NOT VALID` would be the only way back to the prior state, and
there is no scenario identified where that's desirable (the constraint
only rejects a value that should never be written again per migration
256's own intent).

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
- [x] Before writing the row, confirmed it had zero functional footprint:
      `is_driver=false`, no `drivers` row, no `admin_staff` row, zero rides
      as rider or driver.
- [x] After the `UPDATE`, re-queried `count(*)` for all six admin-shaped
      role strings → 0, before running `VALIDATE CONSTRAINT`.
- [x] `VALIDATE CONSTRAINT chk_users_role_not_admin` completed without
      error, confirming no other row in the table violates it either — not
      just the one row this investigation already knew about.
- [ ] Not verified: whether a separate staging/pre-prod Supabase project (if
      one exists) shows the same `auth.users`-empty pattern — this session
      only had access to (and only checked) the one confirmed-production
      project.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — see §8 (code: `git revert`;
      data: a documented single-row `UPDATE` to restore, though not
      recommended).
- [x] Blast radius stated: no code/migration/policy change; one confirmed
      dead-end production row corrected, one constraint validated,
      documentation updated. §4 lists exactly what was touched and why each
      part is safe.
- [x] No silent behavior change — the one functional change (the row's
      `role` value) was already unreachable via every code path except the
      one C109 fixes in the same session; §5 still holds, no user-visible
      effect on any real rider/driver/admin flow.

## What was NOT verified

- Whether any environment other than the one confirmed-production Supabase
  project has a different `auth.users` population (see §9).
- The suggested documentation reword for `backend/tests/rls/`'s docstrings
  and CLAUDE.md's RLS testing-conventions paragraph (C108's "suggested next
  step") — filed as future work, not implemented in this change.
- Whether a Supabase "Third-Party Auth" issuer is configured in the
  project's own Auth settings (C108's corrected entry names this as the one
  gap no SQL query can check) — not checked in this session, no access to
  the Supabase dashboard's Auth settings UI from here.
