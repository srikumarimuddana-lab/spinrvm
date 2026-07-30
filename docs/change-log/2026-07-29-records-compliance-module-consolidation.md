# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Follow-up to the IA recommendation memo delivered this session |

## 1. Issue / gap identified

Four admin-dashboard surfaces that all do the same underlying job (move or report on regulated driver/rider data) — Data Transfer, Compliance, Bulk Operations, Export Approvals — were four separate, unrelated-looking sidebar entries buried in the System group, with no obvious relation to each other. Per the earlier recommendation memo, merging them into one page with tabs carried a specific named risk: the four pages have **different permission requirements** (three are strict `super_admin`-only, one — Compliance — is grantable to non-super-admin staff via a module flag), and two of the four use a client hook that **redirects the whole page to /403 on failure**. Naively rendering all four inside one page would either over-restrict (hide Compliance from staff who should see it) or, worse, redirect a compliance-role staffer away entirely the moment any other tab's hook ran.

## 2. Root cause

Not a bug — this is new IA work. The risk above is a genuine architectural property of the four existing pages that any consolidation has to account for:
- `DataTransferPage` uses `useRequireSuperAdmin()`, which calls `router.replace("/403")` on failure.
- `CompliancePage` uses `useRequireModule("compliance")`, which also redirects on failure.
- `BulkOperationsPage` and `ExportApprovalsPage` use an inline `role === "super_admin"` check that renders a denial card — no redirect.

## 3. Fix / remediation

New page `admin-dashboard/src/app/dashboard/records/page.tsx` renders the four existing page components — imported and reused exactly as-is, not rewritten or duplicated — as tabs, using Radix Tabs' default behavior of only mounting the **active** `TabsContent`. Combined with hiding the `TabsTrigger` for any tab the current user can't access (computed as plain booleans, no redirect side effect, mirroring each embedded page's own check), a user can never select into — and therefore never mount, therefore never get redirected by — a tab they lack permission for. Each embedded page's own internal check remains as a defense-in-depth backstop.

The four old routes (`/dashboard/data-transfer`, `/dashboard/compliance`, `/dashboard/bulk-operations`, `/dashboard/export-approvals`) now 307-redirect (via `next.config.ts`'s `redirects()`) to `/dashboard/records?tab=<slug>` — nothing bookmarked or linked from an old audit-log entry 404s. The sidebar's four separate entries collapsed into one: "Records & Compliance."

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to navigation and page composition.** The four embedded components (`DataTransferPage`, `CompliancePage`, `BulkOperationsPage`, `ExportApprovalsPage`) are imported and rendered unmodified — zero changes to their internal logic, API calls, or permission checks. Grepped for other importers of these four page modules: none exist outside their own route files and this new page, so there's no risk of a second consumer now double-rendering them.
- The sidebar `NAV_GROUPS` change is additive-in-effect: it removes 4 entries and adds 1, using the same `module`/`superAdminOnly` filtering the rest of the sidebar already relies on — no new filtering logic introduced.
- The redirects in `next.config.ts` only affect actual page navigation (HTTP requests to those paths); they do NOT affect the new page's direct component imports of those same files, which happen at the module/bundle level and are unaffected by Next.js routing config.
- No backend change — every API call these four surfaces make is unchanged.

## 5. User-experience effect

- **Internal admin only.** An admin who previously had, e.g., Data Transfer bookmarked is redirected once to the new tabbed page with that tab pre-selected — same content, new URL. A compliance-role (non-super-admin) staffer now sees a single "Records & Compliance" sidebar entry with only the Regulatory Reports tab visible, instead of not knowing which of four cryptically-similar entries applied to them (previously they'd have seen only "Compliance" anyway, so this is a strict improvement, not a new restriction).
- Not visible to riders/drivers/corporate admins.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/records/page.tsx` (new) | Tabbed consolidation page, per-tab visibility gating | Core of the fix |
| `admin-dashboard/next.config.ts` | Added `redirects()` for the 4 old routes | Keep old links/bookmarks working |
| `admin-dashboard/src/components/sidebar.tsx` | Replaced 4 System-group entries with 1 | Matches the new consolidated page |

## 7. Before / after

```tsx
// Before — sidebar.tsx: 4 separate entries, mixed superAdminOnly/module gating
{ href: "/dashboard/data-transfer", label: "Data Transfer", module: "bulk_operations", superAdminOnly: true },
{ href: "/dashboard/bulk-operations", label: "Bulk Operations", module: "bulk_operations", superAdminOnly: true },
{ href: "/dashboard/export-approvals", label: "Export Approvals", module: "bulk_operations", superAdminOnly: true },
{ href: "/dashboard/compliance", label: "Compliance", module: "compliance" },

// After — one entry, visible to either permission group; the page itself
// shows only the tabs a given user can use.
{ href: "/dashboard/records", label: "Records & Compliance", module: "compliance" },
```

## 8. Rollback plan

Plain `git revert` — no schema, no API, no data. Reverting restores the 4 standalone routes and sidebar entries exactly as they were; the redirect rules simply stop existing, so `/dashboard/records` would 404 for anyone who'd bookmarked the interim URL (acceptable — the module only shipped this session).

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded, `/dashboard/records` and all 4 old (now-redirecting) routes compile.
- [x] Manually traced each embedded page's permission-check code path against the new page's visibility booleans to confirm they match exactly (documented in the new page's own file-header comment for future maintainers).
- [ ] Not manually clicked through in a browser in this session (no browser available) — the Radix Tabs "only active TabsContent mounts" behavior is documented Radix behavior, not independently verified by running the app.
- [ ] No test infra exists for admin-dashboard page components in this repo (backend-only pytest convention, per CLAUDE.md) — this change is UI composition only, not independently unit-tested.

## 10. What was NOT verified / deferred

- A per-tab badge count (e.g. pending Export Approvals count on its tab) was mentioned as a nice-to-have in the earlier recommendation memo but not built here — scope was kept to the permission-model fix specifically requested.
- Visual/layout polish of the tabbed page (spacing, whether the negative-margin wrapper around each embedded page looks right in every viewport) was not screenshotted — no visual regression tooling exists in this repo (tracked gap, CR #2829).
