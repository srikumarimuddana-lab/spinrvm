# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (local commit, not yet pushed — see commit SHA in task report) |
| Related issue or gap ID | Decision-log item 3, `docs/audit/2026-08-19-decision-writeups.md` — "Inert `surge`/`pricing` grantable module strings", Recommendation A |

## 1. Issue / gap identified

`"surge"` and `"pricing"` were grantable staff-permission module strings (present in
`AVAILABLE_MODULES` / `ROLE_PRESETS` in `backend/routes/admin/staff.py`, `ALL_MODULES` in
`backend/routes/admin/auth.py`, and the `ALL_MODULES` checkbox picker in
`admin-dashboard/src/app/dashboard/staff/page.tsx`) but no backend route checked either string —
the real surge/pricing admin capability was, and remains, entirely gated by
`require_module("service_areas")`.

## 2. Root cause

The two strings were never wired to a `require_module("surge")` / `require_module("pricing")`
gate anywhere in `backend/routes/`. Surge and pricing admin control lives on the service-areas
router (`PUT /service-areas/{area_id}/surge`, `GET /surge/status`, and the general service-area
`PUT` that carries surge fields), which has only ever checked `require_module("service_areas")`.
This is the exact same defect shape already remediated once for `"heatmap"` (staff.py note,
2026-08-14): a grantable module whose presence or absence in an admin's `modules` array had no
effect on actual backend access — a false sense of *granted* access for an admin holding
`"surge"`/`"pricing"` without `"service_areas"`, and a false sense of *restricted* access for an
admin holding `"service_areas"` without `"surge"`/`"pricing"`.

## 3. Fix / remediation

Retired both strings following the `"heatmap"` precedent exactly (decision-log Recommendation A,
not Option B "wire them up" — no stated business need was found for surge-only/pricing-only admin
scoping distinct from `service_areas`):

- Removed `"surge"` and `"pricing"` from `AVAILABLE_MODULES` and from the `operations` /
  `finance` entries of `ROLE_PRESETS` in `backend/routes/admin/staff.py`, with a comment block
  matching the existing `"heatmap"` note's style and reasoning.
- Removed both from `ALL_MODULES` in `backend/routes/admin/auth.py` (kept in lockstep with
  `staff.py` per `test_backend_grantable_and_jwt_lists_match`). Left the separately-drifted
  `admin-001` refresh-token module literal (lines ~478-496) untouched — it was already documented
  as an intentional, inert drift left alone during the heatmap removal, and remains inert here for
  the same reason (that account is `super_admin`, which bypasses the `modules` array check
  entirely).
- Removed both from the `ALL_MODULES` checkbox picker and the `operations` / `finance` preset
  arrays in `admin-dashboard/src/app/dashboard/staff/page.tsx`, with a comment line matching the
  file's existing `heatmap` / `bulk_operations` / `compliance` removal-history block.
- Updated `backend/tests/test_admin_module_list_parity.py`: removed `"pricing"`/`"surge"` from
  `_KNOWN_UNGATED_GRANTS` (now empty — there is nothing left to pin as "grantable but ungated"
  since neither string is grantable at all anymore) and added both to
  `test_removed_modules_stay_removed`'s parametrized regression pin, alongside the existing
  `"heatmap"` / `"bulk_operations"` entries.
- Trimmed the incidental `"surge"`/`"pricing"` entries out of the module-list fixtures in
  `backend/tests/test_admin_rbac.py` (`FINANCE_MODULES`, `OPERATIONS_MODULES`,
  `SUPER_ADMIN_MODULES`) for consistency with the real presets. Left similar incidental fixture
  literals in `backend/tests/test_admin_business_logic.py` and
  `backend/tests/test_p3_admin_jwt_modules.py` untouched — those are arbitrary JWT-encoding /
  mocked-admin fixtures unrelated to `AVAILABLE_MODULES` parity, not assertions the retirement
  needs to satisfy.
- Marked the decision-log item resolved in `docs/audit/2026-08-19-decision-writeups.md`.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the staff-permission-grant flow.** Grepped the whole repo for
`"surge"` / `"pricing"` as staff-grantable module strings (excluding the unrelated
`require_module("service_areas")` gate, which is untouched, and excluding unrelated uses of the
words "surge"/"pricing" as domain concepts — e.g. `routes/admin/analytics.py`'s surge stats block,
`routes/drivers/profile.py`'s v2 surge payload, `admin-dashboard/src/lib/api/pricing.ts`'s pricing
API client, `admin-dashboard/src/components/analytics/financial-panel.tsx`, the
`cloud-messaging/page.tsx` notification-category selector, `faq-categories.ts`'s FAQ category, and
various test fixtures for the surge *feature* itself). Confirmed consumers of the three module
lists:

- `AVAILABLE_MODULES` / `ROLE_PRESETS` (`staff.py`): read only by the staff create/update handlers
  (filter submitted `modules` against `AVAILABLE_MODULES`) and by `test_admin_module_list_parity.py`
  and `test_admin_rbac.py`. No route handler reads either list to gate a request.
- `ALL_MODULES` (`auth.py`): used to populate a super_admin's JWT `modules` claim on login/refresh,
  and by the parity test. No route handler reads it directly either.
- Frontend `ALL_MODULES` / `ROLE_PRESETS` (`staff/page.tsx`): drives the staff-edit checkbox UI and
  role-preset auto-fill only; not read by any other page.

No other file references `"surge"`/`"pricing"` as a staff-grantable permission. `sidebar.tsx` was
checked specifically (per the heatmap precedent, where a sidebar entry had to be repointed) — no
sidebar entry gates on `module: "surge"` or `module: "pricing"`, so nothing needs repointing there.

**Regression risk:** none identified. The two strings gated nothing, so removing them cannot make
any previously-reachable route unreachable. `test_role_presets_only_reference_grantable_modules`
and `test_no_module_is_grantable_without_gating_something` continue to pass with the strings gone.

## 5. User-experience effect

Internal-admin-facing only; no rider/driver/corporate-admin visibility. Any staff member whose
`modules` array currently contains `"surge"` and/or `"pricing"` loses those checkbox entries the
next time an admin edits their account (existing rows are unaffected until next edit — same
self-cleaning behavior as the heatmap removal, no migration). **No admin loses real capability**:
neither string ever gated a route, so nobody who could previously reach surge/pricing admin
functionality (via `"service_areas"`) loses that access, and nobody who merely held the inert
checkbox loses anything they could actually do. Not visible mid-session — this only affects the
staff-management admin screen and JWT `modules` claim content on next login/refresh.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/staff.py` | Removed `"surge"`/`"pricing"` from `AVAILABLE_MODULES` and from `ROLE_PRESETS["operations"]`/`["finance"]`; added explanatory comment matching the `"heatmap"` precedent | Retire inert grantable strings (decision-log item 3, Recommendation A) |
| `backend/routes/admin/auth.py` | Removed `"surge"`/`"pricing"` from `ALL_MODULES`; added one-line note pointing at the `staff.py` reasoning | Keep `ALL_MODULES` in lockstep with `AVAILABLE_MODULES` per `test_backend_grantable_and_jwt_lists_match` |
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | Removed `"surge"`/`"pricing"` entries from `ALL_MODULES` checkbox list and from `ROLE_PRESETS.operations`/`.finance`; extended the removal-history comment block | Keep frontend picker in sync with backend grantable list |
| `backend/tests/test_admin_module_list_parity.py` | Emptied `_KNOWN_UNGATED_GRANTS` (removed `"pricing"`/`"surge"`); added `"surge"`, `"pricing"` to `test_removed_modules_stay_removed`'s parametrize list | Reflect that both strings are no longer grantable at all; regression-pin against re-adding |
| `backend/tests/test_admin_rbac.py` | Removed `"surge"`/`"pricing"` from `FINANCE_MODULES`, `OPERATIONS_MODULES`, `SUPER_ADMIN_MODULES` fixture lists | Keep test fixtures consistent with the real, now-trimmed presets |
| `docs/audit/2026-08-19-decision-writeups.md` | Added an "Implemented 2026-08-21" note under item 3's Recommendation | Mark the decision-log item resolved |
| `docs/change-log/2026-08-21-retire-surge-pricing-modules-fix.md` | New file (this log) | Required Change Impact & Risk Log for a change to a live-tested admin surface |

## 7. Before / after

```python
# Before — backend/routes/admin/staff.py
AVAILABLE_MODULES = [
    "dashboard", "users", "drivers", "rides", "earnings", "promotions",
    "surge", "service_areas", "vehicle_types", "pricing", "support",
    "disputes", "notifications", "settings", "corporate_accounts",
    "documents", "staff", "audit", "support_tickets",
]
ROLE_PRESETS = {
    "super_admin": AVAILABLE_MODULES,
    "operations": ["dashboard", "rides", "drivers", "surge", "service_areas", "vehicle_types"],
    "support": ["dashboard", "support", "support_tickets", "disputes", "notifications", "users"],
    "finance": ["dashboard", "earnings", "promotions", "corporate_accounts", "pricing", "audit"],
}
```

```python
# After — backend/routes/admin/staff.py
AVAILABLE_MODULES = [
    "dashboard", "users", "drivers", "rides", "earnings", "promotions",
    "service_areas", "vehicle_types", "support",
    "disputes", "notifications", "settings", "corporate_accounts",
    "documents", "staff", "audit", "support_tickets",
]
ROLE_PRESETS = {
    "super_admin": AVAILABLE_MODULES,
    "operations": ["dashboard", "rides", "drivers", "service_areas", "vehicle_types"],
    "support": ["dashboard", "support", "support_tickets", "disputes", "notifications", "users"],
    "finance": ["dashboard", "earnings", "promotions", "corporate_accounts", "audit"],
}
```

## 8. Rollback plan

No feature flag or migration involved — pure code-level permission-list edit, no data written or
mutated. Rollback is a plain `git revert` of this commit: it restores the two strings to
`AVAILABLE_MODULES`/`ALL_MODULES`/`ROLE_PRESETS`/frontend picker/tests, which is safe because
nothing downstream depends on their absence (they were dead strings before this change too, just
in the opposite listed/unlisted state). No live data (Stripe, wallet, ride state, insurance
periods) is touched by this change, so a `git revert` fully suffices here — this is one of the
"genuinely isolated, low-risk changes" the template calls out as not needing a data-level
remediation plan.

## 9. Verification performed

- [x] Automated tests run (unit): `python3 -m pytest backend/tests/test_admin_module_list_parity.py backend/tests/test_admin_rbac.py -q` — all pass (24 tests in the parity file, plus the RBAC suite).
- [ ] Manual repro steps followed in staging — not performed; this is a permission-list-only change with no route behavior change, verified instead by the module-list-parity test suite by design.
- [x] Blast-radius grep performed: `grep -rn '"surge"\|"pricing"'` across `backend/` and `admin-dashboard/` (excluding `node_modules`) — listed and triaged every hit in section 4 above; only the three staff-module-list sites needed changes.
- [x] Reviewed against relevant `CLAUDE.md` convention — this mirrors the documented `"heatmap"` retirement precedent exactly, and doesn't touch the ride state machine, money paths, or RLS.
- [x] Not user-visible/non-trivial in a way that needs a feature flag — this is a pure removal of two inert, non-functional checkbox entries from an internal admin screen; nothing to flag.
- [x] **admin-dashboard build**: a real production build was run — `npm install && npm run build` (Next.js 16.3.1 / Turbopack) — and completed successfully: `✓ Compiled successfully in 38.4s`, the TypeScript check ran clean (no `error TS...` output), and the full route manifest generated including `/dashboard/staff`, with exit code 0. This is a real `next build`, not a dev server or a bare `tsc --noEmit`. `test_admin_ui_offers_exactly_the_grantable_modules` (regex-parses `page.tsx`'s `ALL_MODULES` block) also passed, confirming the frontend list textually matches the backend list post-edit.

## 10. What was NOT verified

- No staging or live-environment check — this change was validated via the existing unit-test suite (`test_admin_module_list_parity.py`, `test_admin_rbac.py`) plus a real `npm run build`; no admin dashboard was run against a live/staging backend to click through the staff-edit screen by hand.
- No visual regression tooling exists for `admin-dashboard` (standing gap, `ACTION_ITEMS.md`) — the checkbox-list shrinking by two entries was reasoned about, not screenshotted.
- Did not re-run the full backend test suite; only the directly affected module-list-parity and RBAC test files were run, per the task's stated verification scope.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data dependency)
- [x] Blast radius is stated, not assumed (see section 4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (section 5 states explicitly: no admin loses real capability, since neither string ever gated a route)
