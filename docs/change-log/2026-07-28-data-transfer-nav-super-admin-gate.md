# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (follow-up on B11/R-A) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | ACTION_ITEMS.md B11/R-A (`docs/privacy/2026-07-28-pia-data-transfer-export.md`); user follow-up asking for the sidebar's stale `bulk_operations` module label to be corrected |

## 1. Issue / gap identified

The Data Transfer and Bulk Operations pages' backend routes require `role == "super_admin"` exactly (`require_super_admin`, added in B11/R-A). But their client-side gates — the sidebar's nav-visibility filter and the Data Transfer page's `useRequireModule("bulk_operations")` — both treat role `"admin"` as equivalent to `"super_admin"` (the existing `isSuperAdmin = role === "super_admin" || role === "admin"` bypass). An `"admin"`-role staff member would see the nav entry, click into the page, have it render, and then get 403'd on every API call inside it — a confusing dead-end rather than the nav simply not showing the entry.

## 2. Root cause

The nav/page client gates were never updated when B11/R-A moved the backend from a module-flag check (`require_module("bulk_operations")`, which the `isSuperAdmin` bypass correctly mirrors) to a stricter `require_super_admin` role check. The client and server gates silently drifted out of sync.

## 3. Fix / remediation

- Added a new `useRequireSuperAdmin()` hook (strict `role === "super_admin"`, no `"admin"` bypass) mirroring the backend's `require_super_admin` dependency exactly.
- `data-transfer/page.tsx` now uses it instead of `useRequireModule("bulk_operations")`.
- Added a `superAdminOnly?: boolean` field to the sidebar's `NavItem` type; both the Data Transfer and Bulk Operations nav entries (and the group/child-item visibility filters) now use it instead of relying on the phantom `bulk_operations` module grant.
- Corrected stale comments claiming `bulk_operations` was "grantable to any staff role through Staff Management" — B11/R-A's investigation already established it's absent from `AVAILABLE_MODULES`/`ALL_MODULES`/`ROLE_PRESETS` and was never actually grantable.

## 4. Risk & impact on existing functionality

- **What else reads `module: "bulk_operations"` or calls `useRequireModule`?** Grepped both files — no other nav entry or page uses `"bulk_operations"` as its module key; `useRequireModule` itself is untouched and still used by every other module-gated page (unaffected).
- **Could this regress a working flow?** No currently-working access changes: `bulk_operations` was never grantable, so `isSuperAdmin` (role `super_admin` or `admin`) was already the only way to see these two pages before this change. The only behavior change is that role-`admin` staff (not `super_admin`) now correctly stop seeing/entering these two pages instead of hitting them and immediately 403ing on every action — a strictly better UX for a case that was already broken, not a new restriction on anyone who could actually use the pages.
- **Blast radius:** isolated to `admin-dashboard/src/components/sidebar.tsx`, `admin-dashboard/src/app/dashboard/data-transfer/page.tsx`, and the new `useRequireSuperAdmin.ts` hook. `bulk-operations/page.tsx` itself already had its own strict `role === "super_admin"` check independent of the nav (confirmed by reading that file) — this change just makes the nav-visibility layer agree with what the page already enforced.

## 5. User-experience effect

- **Who sees a difference:** internal admin only, and only the subset with role `"admin"` (not `"super_admin"`) who previously had the flag `bulk_operations` implicitly denied them nothing extra (it was never grantable) but who were shown these two nav entries anyway via the `isSuperAdmin` bypass.
- **Mid-session visible?** Only on next page load/nav re-render (Zustand auth state, no live session is broken by this).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/hooks/useRequireSuperAdmin.ts` | New hook: strict `role === "super_admin"` client gate | Mirrors the backend's `require_super_admin` dependency for pages that use it instead of `require_module` |
| `admin-dashboard/src/app/dashboard/data-transfer/page.tsx` | Swapped `useRequireModule("bulk_operations")` → `useRequireSuperAdmin()` | Match the backend's actual gate (B11/R-A) |
| `admin-dashboard/src/components/sidebar.tsx` | Added `superAdminOnly` to `NavItem`; Data Transfer + Bulk Operations entries use it; group/child filters check it; corrected stale "bulk_operations is grantable" comment | Nav visibility now matches the real backend access boundary instead of a phantom module flag |

## 7. Before / after

```tsx
// Before
const { allowed } = useRequireModule("bulk_operations");
```

```tsx
// After
const { allowed } = useRequireSuperAdmin();
```

```tsx
// Before (sidebar filter)
return isSuperAdmin || userModules.includes(item.module);
```

```tsx
// After (sidebar filter)
if (item.superAdminOnly) return user?.role === "super_admin";
return isSuperAdmin || userModules.includes(item.module);
```

## 8. Rollback plan

`git-revert-safe` — pure client-side gate change, no schema/API/data change, no feature flag needed.

## 9. Verification performed

- [x] `tsc --noEmit`: 0 new errors (pre-existing errors in unrelated test files, confirmed via `git show origin/main` unaffected by this diff).
- [x] `npm run build` (real production build): completed successfully.
- [x] Blast-radius grep performed: confirmed no other nav entry/page references `bulk_operations` or is affected by the `NavItem` type addition (optional field, no existing entry needs it).
- [x] Reviewed against JWT trust model convention: this is a client-side UX gate only; the authoritative enforcement remains the backend's `require_super_admin` dependency, unchanged by this PR.

## 10. What was NOT verified

- Not manually clicked through in a running browser as an `"admin"`-role test account — reasoned from code (both hooks' logic and the backend dependency source) rather than screenshotted, consistent with this repo's existing gap (no visual regression tooling for admin-dashboard).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`).
- [x] Blast radius is stated, not assumed (§4, grep-verified).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5).
