# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Found during a 2026-09-13/14 deep-dive audit into reported car-marker "not straight on the route" / lag complaints (user-reported, this session) |

## 1. Issue / gap identified

The car marker rendered on the Android Auto head-unit map (`driver-app/lib/androidAuto/carSurface.tsx`) does not snap to the route/road — it can visibly drift off the road, especially in urban canyons or at intersections — even though the identical shared `CarMarker` component renders correctly snapped-to-road on every phone-screen surface (the driver dashboard, `driver-app/app/driver/(tabs)/index.tsx:1425-1441`).

## 2. Root cause

`CarMarker`'s road-snapping (`routeCoordinates` prop → `snapToRoute` in `shared/utils/vehicleTracking.ts`) is opt-in per call site. The Android Auto surface's `<CarMarker>` invocation (`carSurface.tsx:660-682`) only ever passed `coordinate` and `heading` — never `routeCoordinates` — even though the live/stored route polyline (`livePath ?? route.polyline`) is already fetched and drawn on the same map two JSX lines earlier for `<RouteLine>` (`carSurface.tsx:650-651`). The local, hand-written TypeScript type for the lazily-`require()`'d `CarMarker` component (used because `CarMarker.tsx` hard-imports `react-native-maps`, which must not load in a maps-less context) only declared `coordinate`/`heading`, so nothing caught the missing prop at compile time either. This was a wiring gap, not a bug in the shared smoothing/snapping logic itself — that logic is correct and already proven on every other call site.

## 3. Fix / remediation

- `carSurface.tsx`'s local `CarMarker` type declaration now also declares `routeCoordinates?: readonly { latitude: number; longitude: number }[] | null`.
- The `<CarMarker>` JSX call now passes `routeCoordinates={route && (livePath ?? route.polyline).length > 1 ? (livePath ?? route.polyline) : null}` — the exact same live-route-over-stored-route fallback expression `<RouteLine>` already uses two lines above, so the marker and the visible route line are always snapping to/drawing the same polyline.
- `fixTimestampMs` and `fixFeed` (the other two props the phone-screen call site passes) were deliberately **not** added — see §10 alternatives considered.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the Android Auto surface.** `carSurface.tsx` has exactly one `<CarMarker>` call site; no other file was touched. Grepped for every other `<CarMarker` usage in the repo (`driver-app/app/driver/(tabs)/index.tsx` is the only other one) — untouched.
- The shared `CarMarker.tsx` component's own logic is unmodified — this fix only supplies a prop the component already knows how to consume safely as `undefined`/`null` (it treats a missing/null `routeCoordinates` as "no route to snap to," which is exactly the previous behavior, so this is purely additive).
- The route-snapping logic (`snapToRoute`, `MAX_ROUTE_SNAP_M = 35`) already handles a fix that's off-route (falls back to raw/smoothed position) — so a stale or empty `livePath` mid-fetch degrades to the pre-fix behavior for that tick, never crashes or regresses below it.
- Does not touch the already-fixed car-icon bearing logic (ROADMAP R1/R3, `2026-09-12-carmarker-route-rebase-and-android-rotation-port.md`) — `heading` is passed exactly as before, unchanged.

## 5. User-experience effect

Driver-facing, Android Auto head-unit display only (phone screens are unaffected — they already had this). Visible mid-session to a driver who is actively navigating with their phone connected to Android Auto: the car icon should now track the road instead of occasionally floating off it. No other screen, no rider-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/androidAuto/carSurface.tsx` | Added `routeCoordinates` to the local `CarMarker` prop type and to the JSX call, reusing the same live/stored route fallback already used for `<RouteLine>` | Gives the Android Auto marker the same road-snapping every phone-screen surface already has |

## 7. Before / after

```tsx
// Before
<CarMarker
  coordinate={{ latitude: here.latitude, longitude: here.longitude }}
  heading={here.heading}
/>
```

```tsx
// After
<CarMarker
  coordinate={{ latitude: here.latitude, longitude: here.longitude }}
  routeCoordinates={
    route && (livePath ?? route.polyline).length > 1 ? (livePath ?? route.polyline) : null
  }
  heading={here.heading}
/>
```

## 8. Rollback plan

`git-revert-safe` — a pure additive prop; reverting returns the marker to its previous (unsnapped) rendering with zero other side effects. No data, no migration, no flag involved.

## 9. Verification performed

- [x] TypeScript: `npx tsc --noEmit -p tsconfig.json` — exit 0, zero errors (confirms the new prop's type matches `route.polyline`/`livePath`'s actual `{latitude, longitude}[]` shape, sourced from `useCarLiveRoute.ts:51` and `carRoute.ts:26`).
- [x] `npx jest lib/androidAuto` — 14 suites, 236 tests, all passing (no regression to any existing Android Auto behavior).
- [x] Blast-radius grep performed: exactly one `<CarMarker` call site in this file; the only other one (`index.tsx:1425`) is untouched and already correct.
- [x] Read `CarMarker.tsx`'s route-snapping implementation directly (not assumed from memory) to confirm `routeCoordinates` is purely additive to rendering — it does not alter how `coordinate`/`heading` are otherwise consumed.
- [ ] `spinr-edge-case-reviewer` run in parallel against this diff — pending at commit time; any finding will land as a follow-up commit on this same PR before merge.

**What was NOT verified:** no device/emulator/Android Auto head-unit access exists in this session (the same gap `docs/audit/ride-experience/ROADMAP.md`'s R14 already documents for every other marker fix in this feature area) — this fix is code-verified and type-checked only, not watched running on real hardware or the Android Auto emulator. Flagging this explicitly rather than implying full coverage.

## 10. Alternatives considered

- **Also wire `fixFeed`/`fixTimestampMs`** (the other two props the phone dashboard passes): rejected for this fix. `fixFeed` exists to bypass an upstream *render* throttle (`index.tsx`'s dashboard throttles its own re-render to 3000-3500ms) — this surface has no equivalent throttle (`useCarLocation`'s `subscribeCarFix(setLoc)` re-renders on every fix already), so there's no throttle for `fixFeed` to bypass here; adding it would be speculative plumbing with no evidence of benefit. `fixTimestampMs` isn't available at all without a larger change: `CarLatLng` (`carFixChannel.ts:18-23`) carries no timestamp field by design ("There is no timestamp on the legacy shape" — that file's own comment), so surfacing one would mean extending `carFixChannel.ts`'s type and every producer (`carLocationTask.ts`, `backgroundLocation.ts`) — a separate, larger change unrelated to the reported "not on the route" symptom, which `routeCoordinates` alone fully addresses. Left as a follow-up if per-tick playback velocity accuracy on this specific surface is ever reported as a problem.
