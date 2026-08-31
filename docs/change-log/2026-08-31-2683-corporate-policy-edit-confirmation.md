# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | corporate |
| PR / commit link | worktree `worktree-agent-a273bd6549855dcc3`, commits `dbd45d6ca` (backend) and `9bd9ea529` (admin-dashboard) — not pushed/PR'd, per task instructions |
| Related issue or gap ID | GitHub #2683 (Phase B, decision Option 4 — "Admin confirmation UX") |

## 1. Issue / gap identified

A company admin editing a corporate policy (`PUT`/`PATCH /company/{company_id}/policy`) gets no warning that the edit may affect rides already booked under the old policy — they save blind, with no visibility into how many in-flight rides exist.

## 2. Root cause

Phase A of #2683 (already shipped, unmodified by this change) added an audit-only flag in `settle_corporate` when a ride settles under a policy that was edited after booking — but that flag is only ever seen after the fact, in billing data. There was no pre-save signal to the admin at the moment they're about to make the edit.

## 3. Fix / remediation

- **Backend**: new read-only `GET /company/{company_id}/policy/affected-rides-count` endpoint, guarded by the same `require_company_admin` dependency as `replace_policy`/`patch_policy`. Counts the company's rides currently in a pre-pickup status (`SCHEDULED, SEARCHING, DRIVER_ASSIGNED, DRIVER_ACCEPTED, DRIVER_ARRIVED` — imported from `corporate_suspension_service._PRE_PICKUP_STATUSES`, not duplicated) via `db_supabase.get_rows`. Never touches, cancels, or mutates a ride.
- **Frontend**: the company-portal policy page (`admin-dashboard/src/app/company-portal/[id]/policy/page.tsx`) calls this endpoint before submitting a save. If `count > 0`, an `AlertDialog` (the existing shadcn/Radix component already used elsewhere in the admin-dashboard, e.g. `dashboard/corporate-accounts/[id]/page.tsx`) shows: *"This change affects N ride(s) already booked. They will still be charged under the policy that was in effect when they were booked. Continue?"* with Cancel/Confirm. Only on Confirm does the existing `patchCompanyPolicy` PATCH fire. `count === 0` saves immediately, exactly as before — no behavior change for the common case.

Ride state machine, settlement logic (`settle_corporate`), and `corporate_suspension_service.cancel_pre_pickup_rides_for_company` are all untouched. No ride is cancelled or otherwise affected by any part of this change — it is purely an admin-side pre-save confirmation step.

## 4. Risk & impact on existing functionality

- **New backend endpoint is additive** — a new `GET` route under the existing `/company/{company_id}/policy` resource. It reads `rides` via the shared `db_supabase.get_rows` helper (same call shape `cancel_pre_pickup_rides_for_company` already uses) and writes nothing. No other route or background loop calls this new endpoint; blast radius is isolated to this one new route.
- **`_PRE_PICKUP_STATUSES` import** — grepped `backend/` for all consumers of this tuple: `corporate_suspension_service.py` (definer), `corporate_member_offboarding_service.py`, and now `routes/corporate_company.py`. The tuple itself is untouched (imported, not modified), so its existing consumers (company suspension cancellation, member offboarding cancellation) are unaffected.
- **`replace_policy`/`patch_policy` themselves are unmodified** — the actual PATCH/PUT handlers, their audit logging (`corporate_policy_replaced`/`corporate_policy_patched`), and `upsert_corporate_policy` are untouched. The new confirmation step sits entirely in front of the existing PATCH call on the frontend; the backend PATCH/PUT endpoints have no new caller behavior imposed on them.
- **Frontend blast radius**: grepped `admin-dashboard/src` for `getCompanyPolicy`/`patchCompanyPolicy` consumers. Two independent implementations exist:
  - `@/lib/companyApi.ts` (company-portal / rider session) — used by `app/company-portal/[id]/policy/page.tsx`, the only page touched by this change.
  - `@/lib/api.ts` (internal staff admin session) — used by `app/dashboard/corporate-accounts/[id]/policy/page.tsx`, a **separate, untouched** page. Internal staff-admin policy edits still have no pre-save confirmation after this change — that's an intentional scope decision per the task (only the company-portal flow was in scope for #2683 Option 4), not an oversight, but it's a known gap worth flagging if staff-admin policy edits should get the same treatment later.
- No wallet/ride/Stripe state is touched anywhere in this change.

## 5. User-experience effect

- **Company-admin facing** (company portal), visible only in the policy-edit flow, not mid-ride for a rider/driver — the confirmation is a synchronous step in an admin's own edit action, not something that appears unprompted during someone else's active session.
- Common case (no in-flight rides for the company) is unchanged: save still happens on the first click, no new dialog, no added latency beyond one lightweight count call.
- New case (in-flight rides exist): one extra confirmation click before the save proceeds. This is the intended, and only, UX change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_company.py` | Added `GET /policy/affected-rides-count` endpoint; imported `get_rows` and `_PRE_PICKUP_STATUSES` | New read-only count endpoint per #2683 Option 4 |
| `backend/tests/test_corporate_company_routes.py` | Added 3 tests for the new endpoint (filter correctness, zero count, admin-only guard) | Coverage for the new endpoint |
| `admin-dashboard/src/lib/companyApi.ts` | Added `getCompanyPolicyAffectedRidesCount` helper | Frontend client for the new endpoint |
| `admin-dashboard/src/app/company-portal/[id]/policy/page.tsx` | Save flow now checks the count first; shows an `AlertDialog` when count > 0, gating the existing `patchCompanyPolicy` call on confirmation | Admin confirmation UX |
| `admin-dashboard/src/app/company-portal/[id]/policy/page.test.tsx` | New test file: count=0 saves with no dialog; count>0 shows dialog and only saves on confirm; cancel does not save | Coverage for the new UI flow |

## 7. Before / after

```
# Before (page.tsx save())
const save = async () => {
    if (!id) return;
    if (invalidWindow) { setError(...); return; }
    setSaving(true);
    ...
    await patchCompanyPolicy(id, { ... });
    ...
};
```

```
# After (page.tsx save())
const save = async () => {
    if (!id) return;
    if (invalidWindow) { setError(...); return; }
    setError(null);
    const { count } = await getCompanyPolicyAffectedRidesCount(id);
    if (count > 0) { setAffectedCount(count); return; }  // opens AlertDialog
    await applyPolicySave();  // unchanged PATCH body/logic, extracted verbatim
};
```

## 8. Rollback plan

Purely additive on both surfaces — no schema, config, or `app_settings` change, no data written by either new code path.

- **Backend**: delete the new `GET /policy/affected-rides-count` route (or revert the commit). Nothing references it besides the frontend change below, so removing it cannot break any other caller.
- **Frontend**: revert the `page.tsx`/`companyApi.ts` commit. Reverting restores the old direct-save behavior with zero data-level cleanup needed, since no ride or policy row is ever touched by the new code — this is a straightforward `git revert`, unlike a fix touching live ride/wallet state.
- No feature flag was introduced; given the change is a pure UI confirmation step with a brand-new, additive, read-only backend endpoint (not a modification of `replace_policy`/`patch_policy` themselves), a flag was judged unnecessary — reverting the two commits fully restores prior behavior with no residual state to clean up.

## 9. Verification performed

- [x] Automated tests run:
  - Backend: `/tmp/venv/bin/python -m pytest backend/tests/test_corporate_company_routes.py -q --no-cov` — 44 passed (16 selected on `-k affected_rides or policy`, full file 44/44).
  - Frontend: `npx vitest run "src/app/company-portal/[id]/policy/page.test.tsx"` — 3/3 passed. Also ran `npx vitest run src/app/company-portal` (both files in that directory) — 5/5 passed.
  - Frontend typecheck: `npx tsc --noEmit` from `admin-dashboard/` — clean, no errors.
- [ ] Manual repro steps followed in staging — **not done** (see below).
- [x] Blast-radius grep performed: `_PRE_PICKUP_STATUSES` consumers (backend), `getCompanyPolicy`/`patchCompanyPolicy` consumers (frontend) — see §4.
- [x] Reviewed against relevant CLAUDE.md conventions: ride state machine (endpoint is read-only, never transitions a ride), query-filter conventions (`$in` filter via `get_rows`, no `$regex`/escaping concerns), dual-import pattern (both `try`/`except` branches carry the new imports).
- [ ] Feature-flagged — not flagged; see rollback plan for why (pure additive UI + new read-only endpoint, easy plain revert).

## What was NOT verified

- No real Supabase run — the backend test mocks `get_rows` directly; the actual PostgREST `$in` query against a live `rides` table with a real `corporate_account_id` was not exercised.
- No staging check — this is a worktree-only change per the task instructions (not pushed, no PR opened).
- No `npm run build` (production Next.js build) was run for the admin-dashboard change — only `tsc --noEmit` and the targeted vitest run, per the task's explicit instruction not to run the full build for this scope. `tsc --noEmit` passing is not equivalent to a production build succeeding (per CLAUDE.md's own caveat), so treat the frontend change as typecheck+unit-test verified only.
- No visual-regression tooling exists for admin-dashboard's actual behavior today (per CLAUDE.md: the Playwright visual-regression job has zero committed baselines and self-skips) — the new `AlertDialog` was not screenshotted or visually diffed against any baseline; its correctness is reasoned about from the DOM assertions in the vitest test only.
- The internal staff-admin policy page (`dashboard/corporate-accounts/[id]/policy/page.tsx`) was confirmed untouched but was not exercised by any test in this change — it was out of scope for #2683 Option 4 as scoped to the company portal.
- No manual click-through in a running dev server was performed — verification is test-suite-only.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert` of either or both commits; no live data written by the new code paths)
- [x] Blast radius is stated, not assumed (§4 — grepped and enumerated every consumer of the shared tuple and the shared API helpers)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 — the one behavior change, the added confirmation step, is stated explicitly, including that it is gated to a non-zero count so the common case is unchanged)
