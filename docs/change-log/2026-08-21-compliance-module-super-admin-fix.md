# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (background session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (local commit — not yet pushed/PR'd) |
| Related issue or gap ID | `docs/audit/2026-08-19-decision-writeups.md`, section 2 ("`compliance` admin module") — Option B |

## 1. Issue / gap identified

`routes/admin/__init__.py` mounted the whole `compliance_router` (`/api/admin/compliance/*` — GST/PST
remittance, SGI insurance billing, T4A filer handoff, airport report) behind
`Depends(require_module("compliance"))`, but `"compliance"` was absent from `AVAILABLE_MODULES` and
every `ROLE_PRESETS` entry in `routes/admin/staff.py` — a dead module string that reads as
"grantable to some role" but never has been.

## 2. Root cause

`"compliance"` was deliberately removed from the frontend's `ALL_MODULES` picker on 2026-08-14 (an
unresolved product-scoping decision, not an oversight — see the comment this change updates in
`admin-dashboard/src/app/dashboard/staff/page.tsx`), but the backend mount and the frontend's
sidebar/records-page client-side checks were never updated to match. Because the staff create/update
handlers filter submitted modules against `AVAILABLE_MODULES` before persisting
(`staff.py`), no non-super-admin could ever hold the `"compliance"` grant — and because
`require_module()` auto-passes `super_admin` (`dependencies/__init__.py`), the route was reachable by
super_admin only in practice all along. This was a **lockout by omission**, not a **leak**: no
unintended admin could reach the router; the gate merely misrepresented itself as grantable.

## 3. Fix / remediation

Replaced the dead `require_module("compliance")` gate with `require_super_admin`, matching the
existing pattern already used for Data Transfer, Bulk Operations, Export Approvals, and `ai-console`
in the same file — i.e. state the restriction explicitly instead of leaving a module string that
looks grantable but isn't. Updated every client-side surface that mirrored the old gate to match:
- `admin-dashboard/src/components/sidebar.tsx` — "Records & Compliance" nav entry now uses
  `superAdminOnly: true` (same shape as the `ai-console` entry) instead of `module: "compliance"`.
- `admin-dashboard/src/app/dashboard/records/page.tsx` — `hasComplianceModule` (previously
  `isSuperAdmin || (user?.modules ?? []).includes("compliance")`) collapsed to plain `isSuperAdmin`,
  since the `"compliance"` array membership check could never be true for a non-super-admin.
- `admin-dashboard/src/app/dashboard/compliance/page.tsx` — switched from
  `useRequireModule("compliance")` to `useRequireSuperAdmin()` (the same hook the router's sibling
  tabs — Data Transfer, Bulk Operations — already use), since `useRequireSuperAdmin` exists in this
  codebase specifically for pages whose backend route is gated by `require_super_admin` rather than
  `require_module` (see that hook's own docstring).
- Updated the stale comments in `routes/admin/staff.py` (the "OPPOSITE drift" note) and
  `backend/tests/test_admin_module_list_parity.py` (`_KNOWN_UNGRANTABLE_SIDEBAR`,
  `known_unreachable`) that documented this as an open follow-up — it is now resolved.
- Added `backend/tests/test_compliance_super_admin_mount.py` (mirrors
  `test_ai_console_super_admin_mount.py`) asserting the mount itself carries `require_super_admin`,
  that the dead `require_module("compliance")` string is gone, that a module-holding non-super `admin`
  role still 403s, and that a `super_admin` caller clears the gate.
- Updated `test_compliance_reports_http.py`'s `test_compliance_routes_denied_without_module_grant`
  docstring/comment to describe the current (require_super_admin) reasoning rather than the old
  "module grant" framing — the test's behavior and assertion (403 for a non-super `admin` role) were
  already correct and unchanged.
- Marked the decision resolved in `docs/audit/2026-08-19-decision-writeups.md` section 2.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the compliance router and its own frontend surfaces.** Grepped every
  `require_module("compliance")` and `"compliance"` module-string reference across `backend/routes/`,
  `backend/dependencies/`, and `admin-dashboard/src/` after the change: the only remaining
  `"compliance"` occurrences are (a) the `TabSlug`/tab-key identifiers in `records/page.tsx` (unrelated
  string literal — the tab's URL slug and object key, not a module-permission check), and (b) comments
  documenting the historical drift and its resolution. No other route, sidebar entry, or hook reads
  the `"compliance"` module string.
- **No other consumer of `require_module` or `require_super_admin` is affected.** Both dependencies
  are shared, generic functions (`dependencies/__init__.py`) reused by ~20 other router mounts in
  `routes/admin/__init__.py`; this change only touches the one `include_router(compliance_router, ...)`
  call site and does not modify either dependency's implementation.
- **No interaction with the ride state machine, money paths, wallet deltas, or any of the 18
  background loops.** This is a pure admin-authorization change; the compliance router's own handlers
  (report generation, DB reads) are untouched.
- **Additive-safe / zero net access change**: because `"compliance"` was never in `AVAILABLE_MODULES`,
  no admin in production today holds that grant, so no admin loses access they previously had.
  `require_module("compliance")` already auto-passed `super_admin` and rejected everyone else — the
  exact set of callers who can reach `/api/admin/compliance/*` is unchanged (super_admin only, before
  and after).

## 5. User-experience effect

- **Internal-admin-facing only.** No rider, driver, or corporate-admin surface is touched.
- For a `super_admin`: no visible change — the "Records & Compliance" nav entry and the Compliance tab
  behave identically (same routes, same data, same auth outcome).
- For any non-super-admin (including "admin"-role staff holding every other module): no visible change
  either — the nav entry was already invisible to them (gated on an ungrantable module string before,
  gated on `superAdminOnly` now), and the backend already 403'd them on every call.
- Not visible mid-session to anyone already using the app — this only affects the admin dashboard's own
  navigation/auth check, evaluated fresh on each page load.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/__init__.py` | `compliance_router` mount switched from `Depends(require_module("compliance"))` to `Depends(require_super_admin)`, with a comment explaining why | Make the existing super-admin-only restriction explicit |
| `admin-dashboard/src/components/sidebar.tsx` | "Records & Compliance" entry: `module: "compliance"` → `module: "settings", superAdminOnly: true`; comment rewritten | Match the mount change; follow the `ai-console` entry's exact pattern |
| `admin-dashboard/src/app/dashboard/records/page.tsx` | `hasComplianceModule = isSuperAdmin || (user?.modules ?? []).includes("compliance")` simplified to `isSuperAdmin`; doc comment and `useMemo` deps updated | `"compliance"` can never appear in a non-super-admin's `modules` array |
| `admin-dashboard/src/app/dashboard/compliance/page.tsx` | `useRequireModule("compliance")` → `useRequireSuperAdmin()`; import + doc comment updated | Matches the backend gate; matches the sibling Data Transfer/Bulk Operations tabs' existing hook choice |
| `admin-dashboard/src/app/dashboard/staff/page.tsx` | Updated the stale "compliance ... Tracked as a follow-up" comment to record the resolution | Comment was describing an open decision that is now closed |
| `backend/tests/test_admin_module_list_parity.py` | Removed `"compliance"` from `_KNOWN_UNGRANTABLE_SIDEBAR` and `known_unreachable`; comments updated | Both sets existed to pin a *known, deliberate but unresolved* exception; the exception is resolved, so the pins are stale and the underlying condition (an ungrantable module gating a live route) no longer exists |
| `backend/tests/test_compliance_reports_http.py` | Updated comment on `test_compliance_routes_denied_without_module_grant` (no assertion change) | Describe the current require_super_admin reasoning instead of the old module-grant framing |
| `backend/tests/test_compliance_super_admin_mount.py` | New file — mount-wiring test + 403/pass-through request tests | No existing test pinned the mount mechanism itself (only request-level 403s, which look identical whether gated by mount or module string) |
| `docs/audit/2026-08-19-decision-writeups.md` | Added a "Resolution — implemented 2026-08-21" subsection under section 2 | Mark the decision-log item resolved |
| `docs/change-log/2026-08-21-compliance-module-super-admin-fix.md` | New file (this log) | Required by `CLAUDE.md` for any access-control change on a live-tested admin surface |

## 7. Before / after

```python
# Before — backend/routes/admin/__init__.py
admin_router.include_router(compliance_router, dependencies=[Depends(require_module("compliance"))])
```

```python
# After
admin_router.include_router(compliance_router, dependencies=[Depends(require_super_admin)])
```

```tsx
// Before — admin-dashboard/src/components/sidebar.tsx
{ href: "/dashboard/records", label: "Records & Compliance", icon: Upload, module: "compliance" },
```

```tsx
// After
{ href: "/dashboard/records", label: "Records & Compliance", icon: Upload, module: "settings", superAdminOnly: true },
```

```tsx
// Before — admin-dashboard/src/app/dashboard/records/page.tsx
const isSuperAdmin = user?.role === "super_admin";
const hasComplianceModule = isSuperAdmin || (user?.modules ?? []).includes("compliance");
const canView: Record<TabSlug, boolean> = {
    "data-transfer": isSuperAdmin,
    compliance: hasComplianceModule,
    ...
};
```

```tsx
// After
const isSuperAdmin = user?.role === "super_admin";
const canView: Record<TabSlug, boolean> = {
    "data-transfer": isSuperAdmin,
    compliance: isSuperAdmin,
    ...
};
```

```tsx
// Before — admin-dashboard/src/app/dashboard/compliance/page.tsx
import { useRequireModule } from "@/hooks/useRequireModule";
...
const { allowed } = useRequireModule("compliance");
```

```tsx
// After
import { useRequireSuperAdmin } from "@/hooks/useRequireSuperAdmin";
...
const { allowed } = useRequireSuperAdmin();
```

## 8. Rollback plan

No feature flag or DB config is involved — this is a direct code change to an authorization gate, not
data. To revert: `git revert` the commit (backend gate + frontend checks together, since they must
stay in lockstep) and redeploy. This is safe as a plain revert because:
- No live data was touched (no Stripe charge, wallet delta, or ride-state row is affected by an
  admin-authorization mount change).
- The change is a same-net-effect codification (super_admin was already the only caller who could
  reach this router), so reverting simply restores the prior dead-module-string gate with identical
  runtime behavior — not a behavior downgrade.
A `git revert` is an adequate rollback plan here specifically because nothing in this change touches
already-applied live data; per `CLAUDE.md`'s rollback-plan rule, that exemption is being stated
explicitly rather than assumed.

## 9. Verification performed

- [x] Automated tests run — targeted: `backend/tests/test_admin_module_list_parity.py`,
      `backend/tests/test_compliance_super_admin_mount.py` (new),
      `backend/tests/test_compliance_reports_http.py` — see the session's own reported results
      (installed backend deps fresh in this sandbox via `pip install -r backend/requirements.txt`,
      then ran the three files under pytest).
- [x] Blast-radius grep performed — searched `backend/routes/`, `backend/dependencies/`,
      `admin-dashboard/src/` for every `require_module("compliance")` / `"compliance"` module-string
      reference after the change; only non-permission occurrences (tab-slug/tab-key string literals)
      and explanatory comments remain.
- [x] Reviewed against the relevant `CLAUDE.md` convention — this is an admin RBAC change; verified
      against the existing `require_super_admin` precedent already documented at the Data
      Transfer/Bulk Operations/Export Approvals/`ai-console` mounts in the same file, and against
      `useRequireSuperAdmin`'s own docstring on when to prefer it over `useRequireModule`.
- [ ] Feature-flagged — not applicable/not done. This is additive-safe by construction (zero net
      access change, see section 4), so a flag was judged unnecessary rather than skipped by oversight.

## What was NOT verified

- **No production build (`npm run build`) was run for `admin-dashboard`** in this sandbox — only the
  source edits were made and reasoned through; TypeScript types for the three touched files
  (`sidebar.tsx`, `records/page.tsx`, `compliance/page.tsx`) were checked by hand against
  `useRequireSuperAdmin`'s existing signature and the `superAdminOnly?: boolean` field already declared
  on the nav-item type, but no `tsc`/build/lint pass was executed for this change. If a full
  `admin-dashboard` build/typecheck is available in the integrating environment, it should be run
  before merge.
- **Not tested against live Supabase or a running admin-dashboard dev server** — the backend
  verification is `pytest` against `mock_supabase_client`-style fixtures and `TestClient`, not a
  staging/live check; the frontend change was not manually clicked through in a browser.
- **No automated visual/snapshot regression tooling exists for the admin-dashboard surface** (standing
  gap per `CLAUDE.md`) — the sidebar/nav change is reasoned about, not screenshotted.
- Did not audit whether any other codebase surface (mobile apps, `agents/`) references the
  `"compliance"` module string — grep was scoped to `backend/routes/`, `backend/dependencies/`, and
  `admin-dashboard/src/`, which are the only surfaces this permission concept applies to per the
  system's admin-module design.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — section 5 states explicitly that no
      caller's effective access changes
