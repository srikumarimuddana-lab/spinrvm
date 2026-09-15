# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | agent |
| Surface(s) | admin-dashboard, shared |
| Domain (Sentry tag) | admin |
| PR / commit link | (this commit) |
| Related issue or gap ID | Ride-detail modal showed pickup/dropoff pins but no route line |

## 1. Issue / gap identified

After the zero-height map fix, the ride-detail Route Views map showed both pins and basemap tiles, but no orange route. Opening a ride also landed on Actual Trip, so the camera framed two pins (or a GPS fragment) instead of the booked road polyline.

## 2. Root cause

Three stacked causes:

1. Stored polylines can be `[[lat, lng], …]`, `{lat, lng}` objects, numeric strings, or GeoJSON `[lng, lat]`. The modal indexed `p[0]`/`p[1]`, which is `undefined` on objects, so GeoJSON was empty while `hasPlannedTrail` stayed true and skipped the straight-line fallback.
2. The remaining GL line sat under late-loading basemap fills. Pins are DOM markers, so they still showed. `styledata` from `addLayer` also re-ran a full clear+add, wiping the canvas stroke every frame.
3. The modal reset `selectedPhase` to `"actual"` on every open. Actual GPS is often incomplete (`distance_basis: planned_estimated`), so `fitBounds` ran over pins only.

## 3. Fix / remediation

- Normalize stored polylines in the modal via `normalizeDecodedPolyline` (mirrors backend `normalize_polyline_points`).
- Draw the route as an SVG overlay on the MapLibre container (same stacking as pins), and restack GL layers without rebuilding them on every `styledata`.
- Open the modal on Planned Trip so the camera fits the booked road polyline.

## 4. Risk & impact on existing functionality

- Blast radius: single-surface. Callers of `RideRouteMap`: `ride-detail-modal.tsx` only. Invoice PNG maps use `getRideRouteMapDataUrl` (backend), not this component. `normalizeDecodedPolyline` is new in `shared/utils/routeSegments.ts`; grep shows admin modal as the only production importer so far. Not in `docs/known-forks.md`.
- No ride state machine, wallet, or background-loop interaction.
- Could regress: Actual Trip / Pickup still work when those cards are clicked; they keep their own trails. Admins who preferred opening on Actual GPS now start on Planned. Visual-regression job covers `dashboard-rides` (the list), not this modal.

## 5. User-experience effect

- Internal admin only. Visible when opening a ride in the dashboard (including a session already on `/dashboard/rides`).
- Opening a ride now selects Planned Trip and frames that polyline. Pickup / Actual Trip remain one click away.
- No rider/driver/copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/routeSegments.ts` | Add `normalizeDecodedPolyline` | Coerce legacy/object/string/lng-first polylines |
| `shared/utils/__tests__/routeSegments.test.ts` | Tests for the helper | Guard the four stored shapes |
| `admin-dashboard/.../ride-detail-modal.tsx` | Normalize trails; default phase `planned` | Feed drawable points; frame planned on open |
| `admin-dashboard/.../ride-route-map.tsx` | SVG overlay, restack, skip styledata rebuild | Line visible above tiles, not wiped |
| `admin-dashboard/.../ride-route-map.test.ts` | Source-contract tests | Overlay, normalize, planned default |

## 7. Before / after

```
# Before
setSelectedPhase("actual");
plannedTrail = ride.planned_route_polyline.map(p => ({ lat: p[0], lng: p[1] }));
// GL line only; styledata clear+add
```

```
# After
setSelectedPhase("planned");
plannedTrail = normalizeDecodedPolyline(ride.planned_route_polyline);
// SVG overlay + restack; full draw only when layers are missing
```

## 8. Rollback plan

Redeploy the previous admin-dashboard (and shared) build. No DB/migration/Stripe/wallet writes. No feature flag: isolated admin map chrome. `git revert` of this commit is sufficient; nothing was applied to live ride data.

## 9. Verification performed

- [x] Automated tests: `npx vitest run src/app/dashboard/rides/_components/ride-route-map.test.ts` (9 passed; 10 after planned-default case). Shared normalize cases in `shared/utils/__tests__/routeSegments.test.ts`.
- [ ] Manual: planned line confirmed by operator on localhost; this commit also switches the default tab to Planned (not re-clicked in a logged-in browser this turn — login wall).
- [x] Blast-radius grep: `RideRouteMap` importers, `normalizeDecodedPolyline` callers, `setSelectedPhase("actual")`.
- [x] No state machine / money / RLS change.
- [x] Not feature-flagged: admin-only map chrome, additive overlay plus a default-tab change. Asking for a flag would block a local-confirmed visibility fix.
- [ ] Production `npm run build` was **not** run this turn (dev server + unit tests only).

## 10. What was NOT verified

- No logged-in browser pass after switching the default tab to Planned.
- `npm run build` for admin-dashboard was not run.
- Admin visual-regression covers the rides **list** page, not this modal. No automated visual/snapshot tooling exists for the modal itself — the overlay was reasoned about plus operator confirmation that Planned showed a line.
- Live tile host from a Vercel browser was not re-tested.
- Pickup / Actual Trip overlays were not re-clicked after the default-tab change.
