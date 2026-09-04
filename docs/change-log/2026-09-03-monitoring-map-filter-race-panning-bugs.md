# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — "check the settings page and monitoring for other bugs too" |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-portal-heatmaps-audit-gm8fbn` |
| Related issue or gap ID | Same audit as the monitoring `.in_()` batching fix — findings #2, #4, #5, #6 of that report |

## 1. Issue / gap identified

Four further verified bugs on `/dashboard/monitoring`, beyond the unbatched-query root-cause fix in the companion commit:

1. **Area/Vehicle-Type filters permanently dropped drivers from the map.** `applyDriver()` (`page.tsx`) returned before adding a filtered-out driver to `driversMapRef` — the page's own source-of-truth map, which `refreshCounts()` and the "re-apply filters" effect both read from. Switching a filter back to "All" only restored drivers still present in that ref (i.e. ones that matched *some* prior filter state), not the excluded ones — those stayed invisible and undercounted until the next full snapshot.
2. **Toggling an unrelated filter while searching un-hid non-matching markers.** `updateDriverMarker` (`monitoring-map.tsx`) computes visibility with search taking priority over the online/offline toggles, but the separate "re-apply filter visibility when filters change" effect recomputed visibility from online/offline status alone, ignoring `searchQuery` entirely.
3. **The "Follow selected driver" effect had no dependency array**, so it ran after every render of `MonitoringMap` — not just when the followed driver's position or selection changed — fighting a manual pan/zoom gesture and issuing redundant pan calls on every unrelated re-render (a new alert, a filter toggle, the counts poll).
4. **`DriverPanel`'s Rides/Documents tabs silently swallowed fetch failures**, rendering "No rides found"/"No documents found" indistinguishably from a driver genuinely having none.

## 2. Root cause

1. `applyDriver`'s filter checks were written as early `return`s before the `.set()` call, conflating "should this driver's marker be visible" with "should this driver be tracked at all" — the re-apply-filters effect's own logic (`driversMapRef.current.forEach((d) => applyDriver(d))`) already assumed the ref held *every* known driver, filtered or not; only `applyDriver` itself violated that assumption.
2. The re-apply-filter effect was written before `searchQuery`-based filtering existed on `updateDriverMarker` and was never updated to match when that priority rule was added.
3. Diagnosed further than the original audit flagged: adding a dependency array alone would have fixed the wasted re-renders but silently broken real-time driver tracking. Driver position updates are applied entirely imperatively (`driversMap` is a ref, mutated in place via `.set()` — a position update never itself triggers a React re-render of `MonitoringMap`), so the effect's real-time tracking had only ever "worked" by riding on *other*, unrelated re-renders happening to occur often enough to look continuous. A bare dependency array fix would have made the pan reliably fire once (on select/follow-toggle) and then never again as the driver actually moved.
4. Same empty-catch pattern already fixed twice elsewhere on the settings page this session — this instance predates that cleanup and was in a different file.

## 3. Fix / remediation

1. `applyDriver` now unconditionally calls `driversMapRef.current.set(d.id, d)` before running the Area/Vehicle-Type filter checks — those checks now only ever decide whether to show or remove the driver's *map marker* (`updateDriverMarker`/`removeDriverMarker`), never whether the driver is tracked in the ref.
2. The re-apply-filters effect in `monitoring-map.tsx` now computes visibility with the identical `searchQuery`-first rule `updateDriverMarker` already uses, and `searchQuery` was added to its dependency array.
3. Two-part fix, not just a dependency array: (a) the "Follow selected driver" effect now has an explicit `[isLoaded, followMode, selected, driversMap, panTo]` dependency array, so it still fires the *initial* pan the instant a driver is selected or follow mode is toggled on; (b) `updateDriverMarker` itself now issues the pan on every actual marker position update when that driver is the one being followed — moving `panTo`'s declaration earlier in the component so `updateDriverMarker` can reference it, and adding `followMode`/`selected`/`panTo` to `updateDriverMarker`'s own dependency array. Together these restore real-time following without the per-render waste.
4. `DriverPanel`'s tab-loading effect now tracks a `tabError` state alongside `tabLoading`; a fetch failure renders a "Couldn't load rides/documents — the server didn't respond" message with a Retry button (calling the same `loadTabs()` the mount effect uses) instead of the misleading empty-state message.

## 4. Risk & impact on existing functionality

- **Blast radius**: `page.tsx` (finding 1), `monitoring-map.tsx` (findings 2-3), `driver-panel.tsx` (finding 4) — three files, each change isolated to the specific function/effect it fixes. No shared component outside this feature touched.
- `applyDriver`'s change means `driversMapRef` (and therefore `refreshCounts()`'s online/onRide/offline tallies) now include filtered-out drivers again — this is the *intended*, previously-broken behavior (filters were always meant to control marker visibility, not the underlying counted dataset), not a new scope expansion.
- The `updateDriverMarker` pan addition only fires when `followMode && selected?.type === "driver" && selected.id === driver.id` — a no-op for every marker update except the one currently-followed driver, so this adds negligible overhead to the common case (most driver updates aren't for the followed driver).
- `DriverPanel`'s Retry button reuses the exact same `loadTabs()` the mount effect already calls — no new fetch logic, no new failure mode introduced.

## 5. User-experience effect

Admin-facing only (`/dashboard/monitoring`). (1) Switching a service-area or vehicle-type filter back to "All" now correctly restores every driver, not just the ones that happened to match a previous filter state. (2) Toggling "Show Offline"/etc. while actively searching no longer pollutes the results with non-matching markers. (3) Follow mode now tracks a selected driver's real position continuously instead of only panning once (or, previously, panning too eagerly and fighting manual map gestures). (4) A failed Rides/Documents tab fetch in the driver detail panel now shows a clear, retryable error instead of silently claiming the driver has none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | `applyDriver`: `.set()` moved before the filter checks | Filters must control marker visibility only, never ref membership |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.tsx` | Re-apply-filters effect: added `searchQuery` to its visibility rule and deps. Follow-mode: explicit dependency array on the select/toggle effect + a new pan-on-update inside `updateDriverMarker` (with `panTo` moved earlier in the file) | Match `updateDriverMarker`'s own search-priority rule; make follow mode track real driver movement instead of relying on incidental re-renders |
| `admin-dashboard/src/app/dashboard/monitoring/driver-panel.tsx` | Tab-loading effect: added `tabError` state + retry UI on the Rides/Documents tabs | Distinguish "fetch failed" from "confirmed empty" |

## 7. Before / after

```tsx
// Before — page.tsx: filtered-out drivers never enter the source-of-truth ref
if (filters.serviceAreaId && d.service_area_id !== filters.serviceAreaId) {
    mapHandlesRef.current?.removeDriverMarker(d.id);
    return;   // driversMapRef.current.set(d.id, d) never runs
}
...
driversMapRef.current.set(d.id, d);

// After — always tracked; filters only ever affect the marker
driversMapRef.current.set(d.id, d);
if (filters.serviceAreaId && d.service_area_id !== filters.serviceAreaId) {
    mapHandlesRef.current?.removeDriverMarker(d.id);
    return;
}
```

```tsx
// Before — monitoring-map.tsx: follow effect has no deps at all
useEffect(() => {
    if (!isLoaded || !followMode || !selected || selected.type !== "driver") return;
    const d = driversMap.current.get(selected.id);
    if (d?.lat && d.lng) panTo(d.lat, d.lng);
});   // runs after every render

// After — deterministic bootstrap pan on select/toggle...
useEffect(() => {
    if (!isLoaded || !followMode || !selected || selected.type !== "driver") return;
    const d = driversMap.current.get(selected.id);
    if (d?.lat && d.lng) panTo(d.lat, d.lng);
}, [isLoaded, followMode, selected, driversMap, panTo]);

// ...plus real-time tracking tied to actual movement, inside updateDriverMarker:
if (followMode && selected?.type === "driver" && selected.id === driver.id) {
    panTo(driver.lat, driver.lng);
}
```

## 8. Rollback plan

Plain `git revert` — no data, no migration, no schema/API change. All four fixes are pure frontend logic corrections with no new dependencies.

## 9. Verification performed

- [x] Read every cited line directly before fixing (not trusting the audit's report alone) — for the follow-mode finding specifically, traced the actual marker-update path (`driversMap` is a ref, mutated in place, never triggers a re-render on its own) before concluding a bare dependency-array fix would have silently broken real-time tracking, and designed the two-part fix instead.
- [x] `tsc --noEmit` — clean.
- [x] `eslint` on all three changed files — 0 errors; ran the identical lint command against the pre-fix versions (via `git stash`) and confirmed the warning count is unchanged (20 before, 20 after) — no new warnings introduced.
- [x] Real production build (`npm run build`) — exit code 0, confirmed via full-log grep for "error".
- [x] Ran the existing monitoring-adjacent test files (`monitoring-map-demand-fill.test.ts`, `monitoring-toolbar.test.tsx`, the settings/monitoring smoke tests) — 25+1 pass.

## What was NOT verified

- **No live browser reproduction of the filter/search/follow-mode fixes** — no visual-regression tooling exists for admin-dashboard, and this sandbox cannot run a live map against real Supabase/WebSocket traffic. Verified by direct source reading and tracing the actual data flow (ref mutations vs. React state), not by observing the map live.
- **No dedicated regression test was added** for any of the four fixes — this feature's existing test files (`monitoring-map-demand-fill.test.ts`, `monitoring-toolbar.test.tsx`) don't currently exercise `applyDriver`/`updateDriverMarker`/`DriverPanel`'s fetch effect directly, and adding that harness was judged out of scope for this bugfix batch.
