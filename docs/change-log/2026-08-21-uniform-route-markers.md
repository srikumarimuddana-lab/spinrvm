# Change Impact & Risk Log — one route-marker language on every surface

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | srikumarimuddana@gmail.com (Claude Code) |
| Surface(s) | rider-app, driver-app (incl. Android Auto), admin-dashboard (incl. public tracking page), backend (snapshot PNG) |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/android-auto-earnings-privacy-2nzgpp` (51177ff, 575087d, 76c20ad) |
| Related issue or gap ID | Live-testing report: head unit showed a bare red dot for the destination; markers differ per surface |

## 1. Issue / gap identified

The pickup and destination markers looked like a different product on every
surface: 30px disc + Ionicons glyph in the RN apps, bare 16/20px circles (and
'P'/'D' letters on monitoring) on the admin MapLibre maps, a bespoke green
nav-arrow disc + red teardrop on the public tracking page, and Google's named
green/red/orange on the snapshot PNG. On the Android Auto head unit the RN
component rendered its icon font as nothing at all — the destination showed as
a plain red dot.

## 2. Root cause

`shared/constants/routeMapStyle.ts` unified route *colours* but only ever
exported `ROUTE_PIN_COLORS` + a size — it never defined the marker itself. Each
renderer therefore built its own from those two values, and the web surfaces
skipped the glyph entirely because they had no shared element to reuse. The
head-unit failure is separate but related: the shared RN pin depended on
`@expo/vector-icons`, and that font does not paint in the Presentation on the
car's VirtualDisplay.

## 3. Fix / remediation

`ROUTE_PIN_SPEC` / `ROUTE_PIN_GEOMETRY` / `routePinSvg()` are now the
definition of the marker: a coloured disc, a white ring, and a white glyph
drawn as **plain shapes** — pickup **dot**, drop-off **square**, completion
**check** — centre-anchored, with the ring scaling with the marker.

- `shared/components/RoutePins.tsx` draws those shapes with Views (no icon
  font), accepts a `size`, and settles its marker snapshot before switching
  `tracksViewChanges` off. Every RN consumer (rider-app ×6 screens, driver-app
  dashboard + ride detail, Android Auto surface) picks this up unchanged.
- `makeRoutePinEl()` in `admin-dashboard/src/lib/map/maplibre-base.ts` renders
  the same SVG; ride-route-map, live-map and monitoring-map use it.
- The public tracking page (`/track/[rideId]`) drops its bespoke SVGs for the
  shared pin, both ends centre-anchored.
- The backend Static-Maps snapshot uses the shared hex fills. It keeps P/D/C
  letters — Static Maps can only draw its own teardrop with a one-character
  label — and that limit is now stated in the code instead of implied.

## 4. Risk & impact on existing functionality

Blast radius: **cross-surface, display-only.** No ride state, money path, API
contract or stored field is touched; every change is what a map draws.

Consumers checked by grep (`RoutePins`, `ROUTE_PIN_COLORS`, `makeCircleMarkerEl`):
- RN: `rider-app/app/{ride-options,driver-arriving,driver-arrived,ride-in-progress,ride-details,ride-completed}.tsx`, `driver-app/app/driver/(tabs)/index.tsx`, `driver-app/app/driver/ride-detail.tsx`, `driver-app/lib/androidAuto/carSurface.tsx` — all pass points only, so all inherit the new pin with no edit.
- Web: the three admin ride maps + the tracking page. `makeCircleMarkerEl` is deliberately left in place for driver/venue/service-area markers, which are not part of the route language.
- Backend: `render_ride_snapshot_google` / `render_ride_snapshot` only.
- `driver-app/components/dashboard/TripCompletedPanel.tsx` reads `ROUTE_PIN_COLORS` for two list bullets — unchanged, the colours did not move.

Known residual inconsistency, deliberately not changed here: rider-app's
multi-stop waypoint markers (`ride-options.tsx`) are an inline amber dot, which
now shares amber with the completion pin. Different marker type, out of the
scope asked for — flagged rather than silently redesigned.

## 5. User-experience effect

Visible to riders, drivers, corporate/internal admins, and anyone opening a
tracking link — including **mid-session** (a rider watching a live ride sees
the new pins on the next render).

- Same two ends, same two shapes, everywhere. Pickup and drop-off now differ in
  **shape** as well as colour, which is a real accessibility gain on the
  tracking page (colour alone was the only distinction for a colour-blind
  rider).
- On the head unit the destination is a red disc with a white square instead of
  the featureless red dot in the reported photo.
- Admin markers get slightly larger (16→22, 20→26) because a glyph needs room.
- No copy, notification or interaction change; popups/click targets are as they were.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/constants/routeMapStyle.ts` | `ROUTE_PIN_SPEC`, `ROUTE_PIN_GEOMETRY`, `routePinSvg()` | One definition of the marker, font-free |
| `shared/components/RoutePins.tsx` | Shapes instead of Ionicons; `size` prop; snapshot settle | Icon font doesn't paint on the head unit |
| `admin-dashboard/src/lib/map/maplibre-base.ts` | New `makeRoutePinEl()` | Web needed a shared element to reuse |
| `admin-dashboard/.../ride-route-map.tsx`, `.../live/[id]/live-map.tsx`, `.../monitoring/monitoring-map.tsx` | Use `makeRoutePinEl` | Were bare circles / letters |
| `admin-dashboard/src/app/track/[rideId]/page.tsx` | Shared pin, centre-anchored | The rider-facing link was the odd one out |
| `backend/utils/route_snapshot.py` | Shared hex fills + stated degradation | Receipt image matched nothing |
| `shared/constants/__tests__/routeMapStyle.test.ts`, `.../ride-route-map.test.ts`, `backend/tests/*` | Cover the spec; update colour assertions | Lock the uniformity in |

## 7. Before / after

```tsx
// Before — shared/components/RoutePins.tsx
<Ionicons name={icon} size={Math.round(ROUTE_MARKER_SIZE * 0.5)} color="#FFFFFF" />
// …and, in admin-dashboard:
element: makeCircleMarkerEl({ color: ROUTE_PIN_COLORS.pickup, size: 16 })   // no glyph
```

```tsx
// After — one spec, two renderers
<Glyph kind="pickup" size={size} />                       // plain Views
element: makeRoutePinEl({ kind: "pickup", size: 22 })      // routePinSvg()
```

## 8. Rollback plan

Display-only code in the app bundles, the Vercel build and the backend image
renderer — nothing is written to the database, so a revert is complete on its
own. `git revert 76c20ad 575087d 51177ff` restores the previous markers
exactly; web and backend take effect on the next deploy, the apps on the next
build (the previous marker is what installed builds already draw, so no client
is left in a broken half-state meanwhile). No flag, because there is no
mechanism to flag a marker shape on six renderers and the change cannot produce
a wrong route, price or state — only a differently-drawn dot.

## 9. Verification performed

- [x] Automated tests — `shared/constants/__tests__/routeMapStyle.test.ts` (15, incl. 5 new for `routePinSvg`); driver-app `lib/androidAuto` + `__tests__/screens` (20 suites, 219 tests); rider-app route-map screens (8); admin-dashboard full vitest suite (35 files, 339 tests); backend `test_utils_extended.py` + `test_route_snapshot_coverage.py` (249 passed, 1 skipped).
- [x] **Real production build run for admin-dashboard** — `npm run build` completed, `/track/[rideId]` and all dashboard routes compiled.
- [x] `tsc --noEmit` clean for driver-app, rider-app and admin-dashboard.
- [x] Blast-radius grep — `RoutePins`, `ROUTE_PIN_COLORS`, `ROUTE_MARKER_SIZE`, `makeCircleMarkerEl`, `<Marker` across all surfaces.
- [ ] Manual staging check — not done.
- [ ] Feature flag — no (see rollback).

## What was NOT verified

- **No surface was rendered.** No head unit, no simulator, no browser screenshot: the tracking page compiled, it was not opened. Every visual claim (glyph legibility at 22px on an admin map, the check glyph's two rotated bars lining up in RN as they do in SVG, the head-unit pin now painting) is reasoned from the code, not observed.
- This repo has **no visual-regression tooling on any of these surfaces**, so nothing would catch a glyph that lands off-centre. Standing gap.
- The RN check glyph is built from two rotated Views and only approximates the SVG polyline; the two were not compared side by side.
- No mobile production build (`eas build`) was run for rider-app or driver-app.
- The Android Auto icon-font failure is inferred from the reported photo (bare red dot where a flag was expected) — it was not reproduced on a DHU, so "the font does not paint there" remains the working explanation, not a proven one. Drawing shapes is correct regardless.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] UX field filled in for a visible change to already-shipped screens
