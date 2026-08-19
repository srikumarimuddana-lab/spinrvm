# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude (agent session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard (frontend-only fix); backend touched only for a regression test |
| Domain (Sentry tag) | admin |
| PR / commit link | (local worktree commit — not pushed/opened as PR per task instructions) |
| Related issue or gap ID | Ranked blocker #28, `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` finding N16 |

## 1. Issue / gap identified

Two admin-dashboard frontend module-string checks disagreed with the backend's canonical
module strings, silently locking out legitimately-permissioned staff from two admin pages:

1. **Vehicle Types** — the sidebar entry and the page's own `useRequireModule()` gate both
   checked `"pricing"`, but the backend router (`vehicle_fleet_router`) is mounted behind
   `require_module("vehicle_types")` (`backend/routes/admin/__init__.py:152`).
2. **Audit Logs** — the sidebar entry checked `"settings"`, but the audit-log endpoints are
   gated by `require_module("audit")` (`backend/routes/admin/maintenance.py:292,322`). The
   Audit Logs *page* itself (`admin-dashboard/src/app/dashboard/audit-logs/page.tsx:64`) was
   already correctly calling `useRequireModule("audit")` — only the sidebar link was wrong.

This is **not** a security hole (nothing became reachable that shouldn't be) — it is the
opposite failure direction: a staff member granted the correct backend module
(`vehicle_types` or `audit`) either never saw the sidebar link, or saw it but was denied
client-side / 403'd server-side, while a staff member granted the *wrong* module
(`pricing` or `settings`) saw a link that led to a page whose API calls would 403.

## 2. Root cause

`AVAILABLE_MODULES` in `backend/routes/admin/staff.py` intentionally lists `pricing` and
`vehicle_types` as two separate grantable modules (and `settings`/`audit` as two separate
grantable modules) — this is a legitimate, deliberate split in the permission model, not a
duplicate. The frontend's sidebar-nav config and the Vehicle Types page picked the wrong one
of the pair when the routes were originally wired up, and nothing kept the frontend string
and the backend's `require_module()` gate in sync — there was no test asserting the two
sides agree on *which specific module string a given page/link uses*, only that whatever
string a sidebar entry names is *some* grantable module
(`backend/tests/test_admin_module_list_parity.py::test_sidebar_links_gate_on_grantable_modules`).
Both `pricing` and `settings` are real, separately-grantable modules used correctly
elsewhere (see Blast Radius), so the drift passed that test silently — it only checks
membership in the grantable set, not correctness against the specific backend gate a given
page's API calls actually enforce.

## 3. Fix / remediation

Repointed the two frontend module-string references to the backend's canonical strings.
Backend `AVAILABLE_MODULES`, `ROLE_PRESETS`, and all `require_module()` call sites were
**not** touched — the backend was already correct per the audit's framing.

- `admin-dashboard/src/components/sidebar.tsx`: Vehicle Types entry's `module: "pricing"` →
  `module: "vehicle_types"`; Audit Logs entry's `module: "settings"` → `module: "audit"`.
- `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx`: `useRequireModule("pricing")` →
  `useRequireModule("vehicle_types")`.
- `backend/tests/test_admin_module_list_parity.py`: added a new regression test,
  `test_vehicle_types_and_audit_logs_frontend_strings_match_backend_gate`, that parses the
  sidebar and the Vehicle Types page for these exact module strings and pins them to
  `"vehicle_types"` / `"audit"`. Also updated the stale comment on the pre-existing
  `_KNOWN_UNGATED_GRANTS["pricing"]` pin (that comment described the *old*, wrong wiring —
  "pricing" no longer gates the Vehicle Types sidebar link at all after this fix, it gates
  nothing).

## 4. Risk & impact on existing functionality

**Blast radius: isolated to two sidebar entries + one page-level gate.** Grepped every other
reference to the four strings involved (`pricing`, `settings`, `vehicle_types`, `audit`) as a
`module:` / `useRequireModule(...)` value across `admin-dashboard/src`:

- `"pricing"` as a **module gate**: only the two sites fixed here
  (`sidebar.tsx` Vehicle Types entry, `vehicle-types/page.tsx`). `"pricing"` also appears in
  `admin-dashboard/src/app/dashboard/service-areas/page.tsx` (lines 368, 403) as a **UI tab
  `key`/`editTab` value** ("Vehicle Pricing" tab inside the Service Areas edit dialog) —
  unrelated string, not a permission gate, not touched. It also remains a legitimate,
  separate checkbox in the staff-management page
  (`admin-dashboard/src/app/dashboard/staff/page.tsx:55`, `ROLE_PRESETS.finance` includes it)
  and in backend `AVAILABLE_MODULES`/`ROLE_PRESETS` — left as-is; `pricing` is still a
  grantable module, it simply gates no frontend surface today (already tracked in
  `_KNOWN_UNGATED_GRANTS`, comment updated to reflect the new state, not a new defect this
  change introduces).
- `"settings"` as a **module gate**: still used correctly by four *other* sidebar entries
  (`sidebar.tsx`: Redis & Infra, Sentry Issues, Settings, AI Console) that were not touched —
  none of those pages' backend surfaces are gated by `require_module("audit")`, so repointing
  only the Audit Logs entry is correct and does not affect them.
- `"vehicle_types"` as a **module gate**: already used correctly by
  `backend/routes/admin/__init__.py:152` (`vehicle_fleet_router` mount) and by
  `ROLE_PRESETS["operations"]` in `staff.py`; now also used by the two frontend sites fixed
  here. No other frontend consumer of `"vehicle_types"` as a permission-gate string existed
  before this change (other `vehicle_types`/`vehicle.types` hits in the earlier grep were
  API/type names — `getVehicleTypes`, `VehicleType` interface, etc. — not module strings).
- `"audit"` as a **module gate**: already used correctly by the Audit Logs *page* itself
  (`audit-logs/page.tsx:64`, unchanged) and by `backend/routes/admin/maintenance.py`'s two
  `require_module("audit")` calls; now also used by the sidebar entry.

No ride state, wallet/money, dispatch, or background-loop code path is touched. No shared
component used by 3+ pages changed — `useRequireModule` itself (the hook) is unmodified,
only two call-sites' argument strings changed, and `sidebar.tsx`'s nav array entry for these
two specific `href`s.

## 5. User-experience effect

**Internal-admin-facing only** — no rider/driver/corporate-admin-visible change.

- A staff member whose role/custom grant includes `vehicle_types` (e.g. the `operations`
  preset) will now correctly see the Vehicle Types sidebar link and be able to open the page,
  where before they either didn't see the link or saw a client-side 403 redirect.
- A staff member whose role/custom grant includes `audit` (e.g. the `finance` preset) will
  now correctly see the Audit Logs sidebar link, where before it was hidden even though the
  page itself (if reached directly by URL) already worked for them.
- Conversely, a staff member holding only `pricing` (not `vehicle_types`) or only `settings`
  (not `audit`) will now correctly **not** see these links/pages — previously they saw a link
  that led to a page whose API calls silently 403'd, which was itself a confusing dead end.
- Not visible mid-session to an already-logged-in staff member unless they refresh — the
  sidebar renders from the staff member's already-loaded JWT claims (module grants), and JWTs
  are fully trusted per this repo's JWT trust model, so nothing server-side needs to change
  for existing sessions; a staff member's *next* page load reflects the corrected gating.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/sidebar.tsx` | Vehicle Types entry: `module: "pricing"` → `"vehicle_types"`; Audit Logs entry: `module: "settings"` → `"audit"` | Match the backend's actual `require_module()` gate for each page |
| `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx` | `useRequireModule("pricing")` → `useRequireModule("vehicle_types")` | Same — page-level gate must match the API it calls |
| `backend/tests/test_admin_module_list_parity.py` | Added `test_vehicle_types_and_audit_logs_frontend_strings_match_backend_gate`; updated stale comment on `_KNOWN_UNGATED_GRANTS["pricing"]` | Regression pin for this exact fix; keep the comment accurate to the new state |

## 7. Before / after

```tsx
// Before — admin-dashboard/src/components/sidebar.tsx
{ href: "/dashboard/vehicle-types", label: "Vehicle Types", icon: Car, module: "pricing" },
...
{ href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "settings" },
```
```tsx
// After
{ href: "/dashboard/vehicle-types", label: "Vehicle Types", icon: Car, module: "vehicle_types" },
...
{ href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "audit" },
```

```tsx
// Before — admin-dashboard/src/app/dashboard/vehicle-types/page.tsx
const { allowed } = useRequireModule("pricing");
```
```tsx
// After
const { allowed } = useRequireModule("vehicle_types");
```

## 8. Rollback plan

Purely additive-string edits to a frontend-only, JWT-claims-driven gate — no database state,
migration, or Stripe/wallet/ride-state interaction. Revert is a plain code revert (this is
one of the genuinely "isolated, low-risk" cases the template calls out as not needing a
flag/config-based rollback): `git revert <this commit>` restores the previous (buggy but
already-live-tested) strings with a normal redeploy. No feature flag was used because the
change only affects whether an already-permissioned staff member's own sidebar link/page
gate opens correctly — there is no scenario where reverting causes data loss or a
mid-session behavior change for riders/drivers.

## 9. Verification performed

- [x] Automated tests run:
  - Backend: `/tmp/spinr-venv/bin/pytest backend/tests/test_admin_module_list_parity.py -v --no-cov` → **9 passed** (the 6 pre-existing parity tests + 2 pre-existing `test_removed_modules_stay_removed` parametrizations + the 1 new regression test added by this change), 0 failed, 1 unrelated deprecation warning (`httpx`/`starlette.testclient`).
  - Frontend: `npx vitest run src/hooks/__tests__/useRequireModule.test.tsx` → **4 passed** (pre-existing hook tests, unaffected by this change — confirms the hook itself still behaves correctly for admin/super_admin/unauthenticated cases; this repo has no existing sidebar-config or page-level test suite to extend beyond what the backend parity test already covers by parsing the `.tsx` source directly, which is the approach taken here).
- [x] **Production build run**: `npm install` (fresh — `admin-dashboard/node_modules` was not present in this worktree) then `npm run build` (`next build`, Turbopack) in `admin-dashboard/` — completed successfully (`✓ Compiled successfully`, TypeScript check completed with no errors) on the changed files. This is a real `next build`, not just a dev server or `tsc --noEmit`, per this repo's convention.
- [x] Blast-radius grep performed — see Section 4; searched all of `admin-dashboard/src` for `pricing`, `settings`, `vehicle_types`, and `audit` as module/permission-gate strings (not as unrelated identifiers), and named every other consumer found.
- [x] Reviewed against `CLAUDE.md`'s JWT trust model convention (admin JWTs are fully trusted with modules in claims — this change does not touch how those claims are issued or read, only which string the frontend compares them against).
- [ ] Feature-flagged: not applicable / not done. Justification: this is a same-day, 3-line string correction to an already-broken (over-restrictive) internal-admin gate, not a new user-visible behavior; there is no live rider/driver/corporate-facing surface involved, and the `app_settings`-in-DB flag mechanism this repo uses is not wired to sidebar module-gate strings. Rollback is a plain revert (Section 8).

## What was NOT verified

- **Not tested end-to-end against a live Supabase-backed admin session** (i.e. did not log in
  as an actual staff account with a `vehicle_types`-only or `audit`-only grant and click
  through the sidebar in a running app) — verification was source-level (backend regression
  test parses the actual `.tsx` source for the exact strings; `useRequireModule` unit tests
  confirm the hook's admin/super_admin/module-match logic in isolation) plus a real
  `next build` proving the changed files compile and typecheck. No staging environment or
  live admin credentials were available in this sandbox.
- **No visual regression tooling exists in this repo** for admin-dashboard pages (per
  `CLAUDE.md`'s standing gap note) — this change has no visual/layout effect (same label,
  same icon, same href, only the internal `module` string differs), so it was reasoned about
  rather than screenshotted; flagging explicitly per the CLAUDE.md instruction rather than
  letting silence imply a screenshot diff was run.
- **Did not add a new Jest/RTL test that renders `<Sidebar />` or `<VehicleTypesPage />` end
  to end.** The backend's existing `test_admin_module_list_parity.py` already parses these
  exact `.tsx` files by regex and is the established pattern in this repo for pinning
  frontend/backend module-string agreement (see its own docstring and
  `test_sidebar_links_gate_on_grantable_modules`); extending it with a fix-specific
  regression test was judged proportionate for a 2-string fix, and a full component-render
  frontend test was judged disproportionate (the `VehicleTypesPage` component pulls in
  toast/dialog/upload dependencies unrelated to this fix) — stated explicitly per task
  instructions rather than skipped silently.
- **Did not run the full backend test suite** (`pytest` with no path filter) — only the
  targeted parity test file, since this change touches no other backend code path (no
  `AVAILABLE_MODULES`, `ROLE_PRESETS`, or `require_module()` call site was modified).
- **Did not run the full frontend `npm test` (all vitest suites)** — only the directly
  relevant `useRequireModule` suite, plus the full production `next build` (which typechecks
  every file in the project, including the two changed files, and would fail on a type error
  in either).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, Section 8)
- [x] Blast radius is stated, not assumed (Section 4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (Section 5)
