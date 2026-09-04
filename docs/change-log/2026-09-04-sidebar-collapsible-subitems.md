# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (session requested by vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Side-nav "too busy" — reported directly by the user, see the approved mockup at the artifact published this session |

## 1. Issue / gap identified

The admin sidebar (`components/sidebar.tsx`) renders all 41 nav links at once — 27 top-level items plus 14 sub-items — because every parent item's children were always rendered expanded, with no way to collapse them. Drivers (5 children) and Support & Issues (7 children) alone account for 12 of those 14.

## 2. Root cause

No disclosure/collapse mechanism existed for sub-items at all — `NAV_GROUPS`' children arrays were unconditionally mapped and rendered every time their parent group was visible to the signed-in admin's role/module grants.

## 3. Fix / remediation

Sub-items now default to collapsed and expand on click (a chevron toggle next to each parent that has children), with per-admin state persisted in `localStorage` (key `spinr-admin-nav-expanded`) so whichever groups an admin actually lives in stay open across visits. A parent auto-expands whenever it or one of its children is the current route, so navigating directly to a nested page (e.g. `/dashboard/drivers/queue`) never hides where you are.

This was reviewed with the user first as an interactive HTML mockup (before/after, live click-to-expand demo) before any code was written, and approved ("let's implement") before this change.

## 4. Risk & impact on existing functionality

- **Blast radius: single shared component, but imported by every `/dashboard/*` page** (`components/sidebar.tsx` — grepped: no other file re-implements this nav; `Sidebar()` is the sole export used by the dashboard layout). Stated explicitly rather than assumed, per this repo's blast-radius-check convention.
- **No route, permission, or data change of any kind.** Every href, `module`/`superAdminOnly`/`requiresAllModules` gate, and badge count is byte-identical to before — this only changes whether a sub-item list is painted on screen, never whether it exists or who can reach it.
- The collapsed icon-rail sidebar mode (the existing `useSidebarStore().collapsed` global toggle, `w-[68px]` rail) is **completely untouched** — that mode already flattened children as sibling icons regardless of this change, and the new toggle-chevron markup only renders in the non-rail, non-collapsed branch (`!collapsed && hasChildren`).
- An item with no children renders through the exact same (unmodified) `<Link>` branch as before — the diff only introduces a new branch for the 3 parents that actually have children (Drivers, Support & Issues, Help Desk (Zoho)); every other of the 27 top-level items is byte-for-byte unchanged.
- `localStorage` failure (private window, blocked site data) is caught and falls back to the route-based default (open only while active) — never throws, never blocks rendering.

## 5. User-experience effect

Internal-admin-facing only, not visible to riders/drivers/corporate admins. Visually, Drivers/Support & Issues/Help Desk (Zoho) start collapsed instead of always-open — an admin who wants Approvals now clicks the chevron (or the row navigates on click into the parent page, same as before) to reveal it, one extra click versus before. Not mid-session-disruptive in the ride/dispatch sense (this is static chrome, not a live operational view), but it does change what a returning admin sees on load if they don't already have `localStorage` state — flagged here rather than left implied.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/sidebar.tsx` | Added `expandedGroups` state (localStorage-backed) + `toggleGroup`; parents with children now render a Link+chevron row instead of a bare Link, and their child list only renders while `isOpen` | Collapse-by-default disclosure for sub-items, addressing the "too busy" sidebar report |

## 7. Before / after

```tsx
// Before — every parent's children always rendered
{childItems.length > 0 && (
    collapsed ? ( /* icon-rail flatten */ ) : (
        <div className="ml-[18px] pl-3 border-l ...">
            {childItems.map(child => ...)}
        </div>
    )
)}
```
```tsx
// After — children only render while the parent is toggled open
{childItems.length > 0 && (
    collapsed ? ( /* icon-rail flatten, unchanged */ ) : isOpen ? (
        <div className="ml-[18px] pl-3 border-l ...">
            {childItems.map(child => ...)}
        </div>
    ) : null
)}
```

## 8. Rollback plan

`git revert` is sufficient and complete here — no migration, no data, no `app_settings` flag involved. Reverting restores the always-expanded behavior exactly. Not feature-flagged: this is a pure client-side rendering/disclosure change with no route or permission surface, reviewed with the user via an interactive mockup before implementation and explicitly approved — judged low-risk enough that a flag would add process weight without a matching safety benefit. Happy to add one (e.g. gate behind a new `app_settings` bool, same pattern as `admin_theme_v2_enabled`) if the user wants a staged rollout instead.

## 9. Verification performed

- [x] `tsc --noEmit` — clean, no new type errors.
- [x] Full production build (`npm run build`, Turbopack) — succeeded; every `/dashboard/*` route (all of which import the sidebar) compiled with no errors.
- [x] Blast-radius grep performed: confirmed `Sidebar()`/`sidebar.tsx` has no other consumer/re-implementation in `admin-dashboard/`.
- [x] Reviewed the diff for any route/permission/data change — none; confirmed every `href`/module-gate string is unchanged.
- [ ] Manual browser click-through — **not done**; no live browser session available from this sandbox (see the map-verification exchange earlier this session for why). Reasoned about via the interactive HTML mockup shown to and approved by the user beforehand, plus a successful production build, not screenshotted in the real app.

## What was NOT verified

Not visually screenshotted in the actual running admin-dashboard (no browser tooling available in this session). `dashboard-home`/`dashboard-drivers`/etc. are part of the admin-dashboard's active Playwright visual-regression baseline set — since the sidebar renders on every one of those pages, this change **will** produce a real, intended visual diff on next CI run for whichever of the 6 baselined pages show the expanded sidebar state by default (most should now show it collapsed, since none of Drivers/Support/Help Desk's routes are on the active/current path for those baseline pages other than `dashboard-drivers` itself, which auto-expands via the active-route rule). Per this repo's own convention, re-seeding those baselines after an intended UI change needs a human to run `update-visual-baselines.yml` — flagging that now rather than after the fact.
