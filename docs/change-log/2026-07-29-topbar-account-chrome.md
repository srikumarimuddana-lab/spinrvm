# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Explicit follow-up request this session |

## 1. Issue / gap identified

Account identity (name/role/avatar), the dark-mode toggle, and sign-out lived at the very bottom of a scrollable sidebar nav list — easy to lose under a long nav, and off the conventional "account controls in the top corner" pattern most admin tools use. The sidebar collapse control lived in the same footer block.

## 2. Root cause

Not a bug — layout convention gap, explicitly requested to fix this session.

## 3. Fix / remediation

New `components/topbar.tsx`: a sticky header rendered above page content (inside the sidebar-offset wrapper, so it shifts with sidebar collapse/expand same as the page body). Left side: sidebar collapse/expand toggle. Right side: theme toggle + an avatar/name/role button that opens a dropdown (shadcn `DropdownMenu`) with Sign out / Sign out everywhere — the same two actions and confirmation flow the old sidebar footer had, moved verbatim, not reimplemented differently.

Collapse state moved from `Sidebar`'s local `useState` into a new shared `store/sidebarStore.ts` (Zustand, matching the existing `authStore`/`companyAuthStore` pattern) since both `Sidebar` (needs its own width) and `Topbar` (hosts the toggle button) now need to read/write it. Persists to the same `localStorage` key as before, so an existing admin's collapsed/expanded preference carries over unchanged.

## 4. Risk & impact on existing functionality

- **Blast radius: the admin-dashboard shell only** — every one of the 70 dashboard routes renders through `app/dashboard/layout.tsx`, so this is inherently a global-chrome change; grepped for other importers of `Sidebar`, only `layout.tsx` renders it, so no other page independently duplicates the removed footer UI.
- `Sidebar` no longer renders the account/theme/collapse footer at all — moved, not duplicated. A user who had the sidebar footer bookmarked in muscle memory needs to look at the top-right corner instead; this is the explicit ask, not an accidental regression.
- Sign out / sign out everywhere logic (including the confirmation dialog and the server-rejection fallback message) was moved character-for-character into `Topbar`, not rewritten — same behavior, new location.
- `layout.tsx`'s content wrapper changed from `<main>` directly under the sidebar-offset div to `<Topbar /><main>...` — the previous `pt-14 md:pt-6` top padding (compensating for a *fixed* mobile hamburger button overlapping content) is now plain `pt-4`, since the Topbar occupies real document flow height (56px) that content naturally starts below.

## 5. User-experience effect

- **Internal admin only.** Every admin now finds account identity, theme toggle, and sign-out in the top-right corner instead of the sidebar bottom; sidebar collapse/expand moved to the top-left of the header. Functionally identical actions, new location — no permission or behavior change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/store/sidebarStore.ts` (new) | Shared collapsed/toggle state, localStorage-persisted | Needed by both Sidebar and Topbar |
| `admin-dashboard/src/components/topbar.tsx` (new) | New header: collapse toggle, theme toggle, account dropdown | Core of the fix |
| `admin-dashboard/src/components/sidebar.tsx` | Removed footer block (theme/collapse/account/sign-out); consumes `useSidebarStore` instead of local state | Moved to Topbar |
| `admin-dashboard/src/app/dashboard/layout.tsx` | Renders `<Topbar />` above `{children}`; padding adjustment | Wire it in |

## 7. Before / after

```tsx
// Before (layout.tsx)
<Sidebar />
<main className="...">
  <div className="p-4 pt-14 md:pt-6 md:p-8">{children}</div>
</main>

// After
<Sidebar />
<div className="...">
  <Topbar />
  <main className="p-4 pt-4 md:p-8">{children}</main>
</div>
```

## 8. Rollback plan

Plain `git revert` — no schema, no data, no API, no flag. Reverting restores the sidebar-footer layout exactly.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded, all 70 dashboard routes compile with the new layout.
- [x] Grepped for other consumers of `Sidebar`/the removed footer elements — none found outside `layout.tsx`.
- [ ] Not visually verified in a browser in this session (no browser available) — the mobile hamburger-button-over-sticky-topbar interaction, collapse-state CSS var timing, and dropdown menu rendering were reasoned about from the component code, not screenshotted.

## 10. What was NOT verified / deferred

- No automated visual/snapshot regression tooling exists in this repo (tracked gap, CR #2829) — this is a global-chrome change touching every dashboard route, so it's the highest-value candidate yet for that tooling if it's ever built.
- Accessibility (focus order, keyboard nav through the new dropdown, screen-reader labels) was given `aria-label`s on the two icon-only buttons but not tested with an actual screen reader.
