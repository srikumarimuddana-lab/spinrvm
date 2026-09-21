# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | agent |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this commit) |
| Related issue or gap ID | Ride-detail modal Route Views map blank |

## 1. Issue / gap identified

Opening a ride in the admin dashboard showed pickup/actual/planned kilometre cards, but the map slot below them stayed white — no tiles, no pins, no console errors.

## 2. Root cause

`RideRouteMap` sized its MapLibre container with Tailwind `absolute inset-0`. MapLibre's stylesheet sets `.maplibregl-map { position: relative }`, which wins over `absolute`. A relatively positioned empty box does not stretch from `inset-0`, so `clientHeight` was 0. `.maplibregl-map { overflow: hidden }` then clipped the 300px canvas and DOM pins. Confirmed locally: `containerH: 0` with `styleLoaded: true` and a healthy self-hosted style host.

## 3. Fix / remediation

Give the MapLibre container an explicit `height: 100%` / `width: 100%` (same pattern as `geofence-map.tsx`) so MapLibre's `position: relative` cannot collapse it.

## 4. Risk & impact on existing functionality

- Blast radius: isolated / single-surface. Only `RideRouteMap` (imported by `ride-detail-modal.tsx`). Invoice PDF maps use `getRideRouteMapDataUrl` (backend image), not this component. Not in `docs/known-forks.md`.
- No ride state machine, wallet, or background-loop interaction.
- Could regress: if some parent of `RideRouteMap` has no computed height, `height: 100%` would still collapse — the wrapper in this component is already `h-[280px]`, so the percentage has a definite parent.
- Alternative considered: `map.resize()` after the dialog animation (as geofence does). Rejected as the primary fix because logs showed a 0-height CSS box, not a late layout; a timeout would paper over the collapse.

## 5. User-experience effect

- Internal admin only, ride-detail Route Views map.
- Visible mid-session: opening an already-open dashboard tab after deploy will pick up the JS chunk on next navigation/reload; a modal already open would need to be closed and reopened.
- No copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/_components/ride-route-map.tsx` | MapLibre container `absolute inset-0` → `h-full w-full` with inline 100% size | Stop MapLibre CSS from collapsing the canvas |
| `docs/change-log/2026-09-15-admin-ride-modal-map-zero-height.md` | This log | Live-tested admin rides surface |

## 7. Before / after

```
# Before
<div ref={containerRef} className="absolute inset-0" />
```

```
# After
<div
    ref={containerRef}
    className="h-full w-full"
    style={{ height: "100%", width: "100%" }}
/>
```

## 8. Rollback plan

No live data, no feature flag (a blank-vs-visible map is a CSS sizing bug, not a new interaction). Rollback is a Vercel redeploy of the previous admin-dashboard build, or reverting this commit. No Stripe/wallet/ride-state remediation.

## 9. Verification performed

- [x] Runtime logs on local `next dev`: MapLibre constructed, basemap loaded, `containerH: 0` before the fix; admin confirmed maps paint after the height change
- [ ] Automated tests run — none exist for this MapLibre container; not added (visual job below does not open the modal)
- [x] Blast-radius grep: `RideRouteMap` / `ride-route-map` — sole UI consumer is `ride-detail-modal.tsx`
- [x] Reviewed against CLAUDE.md admin visual-regression note
- [x] Not feature-flagged: restoring a previously-shipped map, not new UX
- Production `npm run build` was **not** run this session (dev-server + runtime logs only)

## 10. What was NOT verified

- No production/staging deploy yet — this change was local-only until this commit
- Admin visual-regression job covers `dashboard-rides` (the list page) and does **not** open the ride-detail modal, so a modal map paint will not fail or update that baseline
- rider-app / driver-app maps are untouched
- Tile-server reachability from Vercel browsers was not re-tested in production
