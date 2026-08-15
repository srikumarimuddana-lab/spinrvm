# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-15 |
| Author | Claude Code (session: imported-rides-map-generation) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | rides / admin |
| PR / commit link | branch `claude/imported-rides-map-generation-bwqjxm` (PR #3927) |
| Related issue or gap ID | follow-on to `2026-08-14-imported-ride-polyline-shape.md` |

## 1. Issue / gap identified

The admin ride drawer's "Route Views" offers three tabs — Pickup, Actual Trip,
Planned Trip. Legacy-imported rides have geometry for **only** the planned view.
On the other two, the map fell through to its straight-line fallback and drew a
pickup→dropoff line in the standard orange→red route styling — visually
indistinguishable from a recorded GPS trace, on the same screen used for SGI and
dispute review. The distance figures also read "—" with no explanation.

## 2. Root cause

Two separate gaps, both from imported rides never being considered on this screen:

1. `RideRouteMap` draws a straight pickup→dropoff gradient whenever no other
   geometry exists. That is reasonable for a live ride still awaiting GPS; for an
   imported ride, GPS will *never* arrive, so the fallback asserts a path that was
   never recorded.
2. The Planned card reads `planned_distance_km`, which imports never populated
   (1 of 187 rows). The OSRM backfill wrote the road distance to `distance_km`
   instead, so a real, known distance rendered as "—".

Verified against production — imported rides (n=187) hold:

| Field | Populated |
|---|---|
| `planned_route_polyline` | 187 |
| `distance_km` | 187 |
| `phase_polylines` | 0 |
| `route_polyline` | 0 |
| `actual_distance_km` | 0 |
| `planned_distance_km` | 1 |
| `ride_routes` (v2) row | 0 |

## 3. Fix / remediation

- Derive `isImported` from `legacy_import_metadata` (same signal the ride list
  already badges on) in the ride detail modal.
- Pickup / Actual views on an imported ride: label "(not captured)", show
  "Imported from the previous app — no GPS was recorded for this ride", and pass
  the new `suppressStraightFallback` so the map renders **markers only**.
- Planned card: fall back to `distance_km` when `planned_distance_km` is absent,
  **only** for imported rides.
- Pickup/Actual card subtitles read "Not captured (imported)" instead of "—".
- "Imported" badge on the Route Views header, matching the ride-list badge.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to one admin screen.**

- `RideRouteMap` has exactly **one** consumer — `ride-detail-modal.tsx` (grepped
  across `admin-dashboard/src`; the only other `RideRouteMap` hit is the unrelated
  `getRideRouteMapDataUrl` API import in `ride-invoice.tsx`).
- `suppressStraightFallback` defaults to `false`, so behavior for every
  non-imported ride is byte-for-byte unchanged. Purely additive prop.
- The `distance_km` fallback is gated on `isImported`, so no organically-created
  ride's Planned figure changes.
- No backend, API, schema, or migration change. No money, ride-state, dispatch, or
  insurance-period code path touched.
- The email-receipt route image (`/rides/{id}/route-map.png`) is a separate
  backend renderer and is unaffected.

Residual risk: `isImported` keys off `legacy_import_metadata` being non-empty. A
future import that leaves it `{}` would not be detected and would fall back to the
old behavior — degraded to today's state, not worse.

## 5. User-experience effect

- **Internal admin only.** No rider, driver, or corporate surface changes.
- An admin opening an imported ride now sees markers plus an explicit "not
  captured" note on the two GPS tabs, instead of a route-styled line that was never
  driven; and a real distance on the Planned tab instead of "—".
- **Mid-session visibility**: none — historical completed rides only.
- Copy added: "Not captured (imported)", "Driver → Pickup (not captured)",
  "Pickup → Dropoff (not captured)", "Imported from the previous app — no GPS was
  recorded for this ride", "Planned route (imported)", "Planned route only — no GPS
  captured". Plain, specific, non-technical; states what happened and why.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/rides/_components/ride-detail-modal.tsx` | `isImported` flag; imported branch for Pickup/Actual; `distance_km` fallback on Planned; badge + subtitles | Stop asserting a route that was never recorded; surface the distance we do have |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-route-map.tsx` | New optional `suppressStraightFallback` prop (default `false`) | Let the caller say "absence of geometry is the finding" |

## 7. Before / after

```tsx
// Before — imported ride, Actual tab: no geometry, so the map drew a
// straight pickup→dropoff line in full route styling.
if (!hasPlannedTrail && !hasRouteGeometry && !(locationTrail && locationTrail.length > 1)) {
    /* draws buildStraightRouteGradient(...) */
}
```

```tsx
// After — caller can suppress it; markers only, with an explicit hint.
if (!suppressStraightFallback && !hasPlannedTrail && !hasRouteGeometry && !(locationTrail && locationTrail.length > 1)) {
```

```tsx
// Before — always "—" for imported rides
km: fmtKm(ride.planned_distance_km)
// After — the OSRM road distance we actually have
km: fmtKm(ride.planned_distance_km ?? (isImported ? ride.distance_km : undefined))
```

## 8. Rollback plan

Frontend-only, no persisted state and no data written — reverting the two files
restores prior behavior exactly, with no data-level remediation. Not feature-flagged
(see §9 for the justification); if a flag is wanted before merge, the natural gate is
`suppressStraightFallback={importedNoGps && <flag>}`, which collapses to today's
behavior when off.

## 9. Verification performed

- [x] **Production build run**: `npm run build` in `admin-dashboard` — exit 0.
      (Not just `tsc --noEmit`, though that also passes clean.)
- [x] ESLint on both changed files: **0 errors**. 6 warnings, all pre-existing and
      unrelated (`set-state-in-effect` at line 104, two `no-img-element`).
- [x] Blast-radius grep: `RideRouteMap` consumers across `admin-dashboard/src`
      (one), `legacy_import_metadata` consumers (ride list, users, drivers pages).
- [x] Data census against production confirming which fields imported rides
      actually populate (table in §2) — the fix is built on measured data, not
      assumption.
- [x] Confirmed `get_ride_details_enriched` uses `select("*")`, so
      `legacy_import_metadata` is present on the detail payload.
- [ ] Not feature-flagged. Justification: internal-admin-only, single consumer,
      additive prop defaulting to existing behavior, and the change replaces
      misleading output with accurate output — gating it would keep the misleading
      state reachable.

## 10. What was NOT verified

- **No browser check.** The rendered result was not opened or screenshotted; the
  behavior is reasoned from the code path plus a passing production build. This
  repo has no visual-regression tooling for admin-dashboard (standing gap,
  `ACTION_ITEMS.md`), so "no visible diff for non-imported rides" is an argument
  from the default-`false` prop, not an observed result.
- **No automated test.** There is no existing test file for `ride-detail-modal.tsx`
  or `ride-route-map.tsx`, and none was added — this is presentational logic behind
  a MapLibre canvas with no current harness. Worth flagging rather than implying
  coverage.
- **Non-imported rides not re-checked in a running app.** Unchanged behavior is
  established by the default-`false` prop and the `isImported` gate, not by
  clicking through a live ride.
- **Only imported rides in this dataset were considered.** A ride that is partially
  imported (some GPS present) was not tested — none exist in production today.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (revert two presentational files)
- [x] Blast radius is stated, not assumed (single consumer, enumerated)
- [x] UX effect filled in — this changes what an already-shipped admin screen shows
