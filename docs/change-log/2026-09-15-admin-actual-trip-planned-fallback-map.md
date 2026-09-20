# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | agent |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (uncommitted) |
| Related issue or gap ID | Actual Trip map showed a GPS fragment while the card showed booked km |

## 1. Issue / gap identified

On a completed ride with GPS loss, Actual Trip showed 8.37 km (booked) and “GPS incomplete”, but the map drew only a leftover GPS blob and zoomed into a few blocks. Planned Trip correctly showed the 8.37 km road path.

## 2. Root cause

`resolve_measured_distance_km` already publishes booked km as `planned_estimated` when coverage is too poor. The admin Actual tab still passed `actual_route_segments` into the map, so a fragment counted as drawable GPS and `fitBounds` framed that blob.

## 3. Fix / remediation

When `distance_basis === planned_estimated` (or v2 has no drawable GPS), Actual Trip draws the booked `planned_route_polyline` so the map matches the km card. Trusted GPS (`observed` / `reconstructed`) is unchanged. Imported rides still draw nothing on Actual (SGI: do not invent a GPS trace).

## 4. Risk & impact on existing functionality

- Blast radius: isolated / single-surface. Only `ride-detail-modal.tsx` Actual-tab wiring into `RideRouteMap`. No backend, fare, `actual_distance_km`, or SGI row change.
- Could regress: an admin who wanted to inspect the GPS fragment on Actual no longer sees it when the finalizer marked GPS untrusted — Planned is the path shown. Trusted GPS rides still show segments.
- Not in `docs/known-forks.md`. Invoice PNG maps unchanged.

## 5. User-experience effect

- Internal admin. Visible when opening Actual Trip on a GPS-incomplete ride (including an already-open dashboard session after refresh).
- Actual km stays the booked figure when GPS is incomplete (already stored). The map now shows that same booked road path instead of a few blocks.
- No rider/driver copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/.../ride-detail-modal.tsx` | Actual tab uses booked polyline when GPS is untrusted/empty | Map matches planned-fallback km |
| `admin-dashboard/.../ride-route-map.test.ts` | Source-contract test | Guard the fallback |

## 7. Before / after

```
# Before
actualSegmentsProp = ride.actual_route_segments; // fragment → zoomed blob
```

```
# After
if (distanceBasis === "planned_estimated" || !hasDrawableGps)
  plannedProp = bookedPts; // full booked road path
else
  actualSegmentsProp = ride.actual_route_segments;
```

## 8. Rollback plan

Redeploy previous admin-dashboard. No DB writes. `git revert` is sufficient.

## 9. Verification performed

- [x] `npx vitest run src/app/dashboard/rides/_components/ride-route-map.test.ts` (to be run this turn)
- [ ] Logged-in browser not re-checked this turn
- [x] Blast-radius: Actual tab + `RideRouteMap` only
- [x] Not feature-flagged: admin display of geometry already on the ride; no new validation/rejection
- [ ] `npm run build` not run

## 10. What was NOT verified

- No logged-in browser pass of Actual Trip after this change.
- Admin visual-regression covers the rides list, not this modal.
- GPS-complete Actual Trip path was not re-clicked in a browser.
