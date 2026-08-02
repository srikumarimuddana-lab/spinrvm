# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Admin #4 |

## 1. Issue / gap identified

`admin-dashboard/src/components/sidebar.tsx`'s `isSuperAdmin` check
treated `role === "admin"` as equivalent to `role === "super_admin"` for
nav-item visibility: `const isSuperAdmin = user?.role === 'super_admin'
|| user?.role === 'admin'`. "admin" is a real, separate role in the
backend's `_admin_roles` set (`dependencies/__init__.py`) — but unlike
`super_admin`, it does **not** bypass `require_module()` on the backend;
it's scoped by its own `modules` grant exactly like
`operations`/`support`/`finance`/`custom`. So an "admin"-role staff
member with, say, only the `drivers` module saw every nav entry across
the entire sidebar (Corporate, Staff, Settings, Records & Compliance,
etc.) — clicking any of them would 403 on the very first API call.

While investigating, the same conflation turned up in 3 more places
sharing the identical root cause: `hooks/useRequireModule.ts` (the
dashboard's own documented "authoritative client-side enforcement
layer" — its docstring literally claimed "super_admin / admin roles →
always allowed," which is the more consequential instance since it gates
actual page rendering, not just a nav link), and two page-level inline
checks in `dashboard/records/page.tsx` and `dashboard/earnings/page.tsx`
that manually replicated the same wrong bypass instead of using the
hook consistently.

## 2. Root cause

Someone at some point assumed "admin" was a synonym for "full-access
admin" (perhaps carried over from before the module-grant RBAC system
existed), baked that assumption into `useRequireModule.ts`'s own
docstring as if it were the documented contract, and the same wrong
assumption then propagated by copy/analogy into `sidebar.tsx` and the
two page-level inline checks. The backend never agreed: `require_module`'s
own docstring (already read earlier in this same review, C3/H6) says
"super_admin always passes regardless of the modules claim" — no mention
of "admin." The bootstrap legacy super-admin account (`admin-001`) is
also minted with `role: "super_admin"`, not `"admin"` (confirmed via
`routes/admin/auth.py`), so the `"admin"` fallback was never even needed
to cover that case — it was purely an incorrect widening.

## 3. Fix / remediation

- `sidebar.tsx`: `isSuperAdmin` now checks `role === 'super_admin'` only.
  Updated the stale nearby comment describing the old (wrong) semantics.
- `hooks/useRequireModule.ts`: same fix, plus corrected the hook's own
  docstring (which had asserted the wrong contract) and added a note
  explaining why the bug was UX-misleading rather than a data leak — the
  backend's own `require_module` enforcement on every real API call was
  always the actual authority, so no data was ever exposed by this bug;
  a role="admin" user without the module could reach the page shell and
  see empty/error states, never real data.
- `dashboard/records/page.tsx`: `hasComplianceModule` no longer bypasses
  for `role === "admin"`.
- `dashboard/earnings/page.tsx`: `canSeeReferrals` no longer bypasses for
  `role === "admin"` — ironically, this file's own comment already said
  "otherwise they'd see the tab and hit a 403," describing exactly the
  bug its own code had.
- Explicitly did **not** touch the several `role === "admin"` checks in
  `company-portal/**` and `lib/companyApi.ts` — those check a completely
  different enum (`MemberRole`: `owner`/`admin`/`member`, a corporate
  company's membership role), where `"admin"` correctly means "company
  admin member." Confirmed via grep and by cross-referencing
  `schemas/corporate.py`'s `MemberRole` enum from earlier in this
  session — unrelated domain, correctly implemented, out of scope.

## 4. Risk & impact on existing functionality

- **Blast radius: 4 files, all the identical one-clause removal.**
  Grepped every `role === "admin"` / `role === 'admin'` occurrence across
  the entire admin-dashboard `src/` tree (see §3) to confirm exactly
  which instances share this root cause vs. which check the unrelated
  corporate-membership role.
- **Behavior change: an "admin"-role staff member without a given
  module no longer sees that module's nav entry or page.** This is the
  fix's entire purpose — removing access the backend was already
  refusing, just previously shown/reachable in the UI shell first. No
  change for `super_admin` (still sees everything) or for any role
  correctly scoped by an explicit `modules` grant (still sees exactly
  what they're granted).
- No data-exposure risk closed or introduced: every real data-fetching
  API call on every affected page already went through the backend's
  `require_module` dependency regardless of what the frontend showed —
  confirmed via `useRequireModule.ts`'s own (now-corrected) docstring,
  which states the backend is the authoritative enforcement layer.
- Added 4 new unit tests directly exercising `useRequireModule`'s fixed
  behavior (admin-without-module blocked + redirected to /403,
  admin-with-module allowed, super_admin always allowed, unauthenticated
  redirected to /login). Ran the full dashboard smoke-test suite (20
  tests spanning every dashboard page, including
  `records`/`earnings`/every page `useRequireModule`-gated) plus the new
  hook tests — 24 total, all passing. The smoke suite mocks
  `role: "super_admin"` throughout, so it doesn't newly exercise the
  admin-without-module path itself, but confirms zero regression for the
  unaffected (super_admin) case across every page in the app.

## 5. User-experience effect

**Internal admin-facing only.** A staff member with `role: "admin"` and
a narrow `modules` grant now sees a sidebar and page set that accurately
matches what they can actually use, instead of a full nav tree where
most entries 403 on click. No change for `super_admin` users (unchanged,
full access) or for any role using the module-grant system as intended
(operations/support/finance/custom — always correctly scoped).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/sidebar.tsx` | `isSuperAdmin` dropped the `role === 'admin'` bypass; stale comment corrected | Nav visibility must match actual module access |
| `admin-dashboard/src/hooks/useRequireModule.ts` | Same fix in the canonical page-access-gating hook; docstring corrected | This is the real enforcement layer, not just cosmetic |
| `admin-dashboard/src/app/dashboard/records/page.tsx` | `hasComplianceModule` dropped the same bypass | Same root cause, inline duplicate of the pattern |
| `admin-dashboard/src/app/dashboard/earnings/page.tsx` | `canSeeReferrals` dropped the same bypass | Same root cause, inline duplicate of the pattern |
| `admin-dashboard/src/hooks/__tests__/useRequireModule.test.tsx` (new) | 4 tests covering the fixed hook | Lock in the fix on the highest-impact instance |

## 7. Before / after

```tsx
// Before — sidebar.tsx / useRequireModule.ts
const isSuperAdmin = user?.role === 'super_admin' || user?.role === 'admin';
```

```tsx
// After
const isSuperAdmin = user?.role === 'super_admin';
```

```tsx
// Before — records/page.tsx
const hasComplianceModule = isSuperAdmin || user?.role === "admin" || (user?.modules ?? []).includes("compliance");

// After
const hasComplianceModule = isSuperAdmin || (user?.modules ?? []).includes("compliance");
```

```tsx
// Before — earnings/page.tsx
const canSeeReferrals =
    user?.role === "super_admin" || user?.role === "admin" || (user?.modules ?? []).includes("drivers");

// After
const canSeeReferrals =
    user?.role === "super_admin" || (user?.modules ?? []).includes("drivers");
```

## 8. Rollback plan

Plain frontend code change, no migration, no data written. `git revert`
fully restores the prior (over-broad) behavior across all four files in
one commit. No feature flag — this closes an access-model mismatch
between frontend UI state and the backend's actual (and always-
authoritative) enforcement; there's no meaningful dark-ship version of
"stop showing an admin-role user nav entries they can't use."

## 9. Verification performed

- [x] Automated tests: `useRequireModule.test.tsx` (4 new),
      `pages.smoke.test.tsx` (20, unaffected — mocks `super_admin`
      throughout) — 24 passed, run via `vitest run`.
- [x] `eslint` on all 4 touched files — 0 errors; the warnings present
      are pre-existing and on unrelated lines, confirmed by re-running
      `eslint` against the unmodified `sidebar.tsx` via `git stash` (same
      warning, same file, only the line number shifted from the added
      comment lines).
- [x] `tsc --noEmit` — 27 pre-existing, unrelated errors only (confirmed
      via `grep` for all 4 touched file paths — zero matches).
- [x] Blast-radius grep performed (see §3): every `role === "admin"`
      occurrence in the admin-dashboard, explicitly distinguishing the 4
      platform-admin-role instances (fixed) from the unrelated corporate-
      membership-role instances (untouched, confirmed correct).
- [ ] Did not run a real production build (`npm run build`) — only
      `tsc --noEmit` + `eslint` + `vitest`, consistent with this review's
      established lighter-weight verification for admin-dashboard
      changes; no staging access, no live browser click-through with a
      real `role: "admin"` staff account.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — every occurrence of the
      pattern grepped, categorized, and either fixed or explicitly ruled
      out with the reasoning stated
- [x] User-experience effect stated: this is a real, intentional
      narrowing of what an "admin"-role user sees (removing UI-only
      access to pages they were always refused actual data on) —
      documented as the fix's purpose, not an accidental side effect

## What was NOT verified

Did not manually test in a live/staging environment with a real
`role: "admin"` staff account and a narrow `modules` grant — verified via
unit tests against the extracted hook logic and static analysis
(grep/read) of the 3 page-level call sites, not an end-to-end click-
through. Did not add dedicated component-level tests for `sidebar.tsx`,
`records/page.tsx`, or `earnings/page.tsx` themselves (only the shared
`useRequireModule` hook, which encodes the identical logic) — rendering
those three components in isolation would require substantial additional
mocking (icons, Zustand stores, multiple API modules) disproportionate
to the value given the hook test already proves the exact fix pattern
correct and the existing smoke suite already renders all three pages
end-to-end (with `super_admin`, so it wouldn't have caught this bug in
either direction, but does confirm no regression to the unaffected
case). Did not search less-common surfaces (e.g. any admin-dashboard
code outside `src/`) for the same conflation pattern.
