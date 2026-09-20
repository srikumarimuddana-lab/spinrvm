# Change Impact & Risk Log — Android car marker frozen at trip start

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | Cursor Grok 4.6 session |
| Surface(s) | driver-app, rider-app (`shared/components/CarMarker.tsx`) |
| Domain (Sentry tag) | drivers / rides |
| PR / commit link | uncommitted at write time |
| Related issue or gap ID | Live Android driver test 2026-09-15: car stuck, polyline disappears |

## 1. Issue / gap identified

On the current Android driver binary the car icon stays at the position it had when the map first painted. The route line erases as the driver travels. Kill/reopen jumps the icon to the real location, then it freezes again. iOS is unaffected.

## 2. Root cause

Two stacked changes landed after the last working Android driver binary (android-auto, minify off, runtime 2.7.0, no Fabric `markerRotation` patch):

1. **PR #5428 (2026-09-14)** added Fabric `markerRotation` / `flat` so Android heading actually reaches Google Maps. Until then, per-frame React `rotation` updates were ignored by the native schema.
2. **This production binary also turned R8 on** (`SPINR_ANDROID_MINIFY=1`). The previous internal driver build did not.

The marker tick treated a truthy JS `animateMarkerToCoordinate` as “native move succeeded.” On react-native-maps 1.27 that method always exists on the ref; Fabric often no-ops or throws, and later `coordinate` prop diffs on custom markers are not applied. Combined with a requestAnimationFrame loop that set `rotation` every frame, Fabric started delivering those rotation diffs (post-#5428) while position stayed at the mount pin.

The polyline shrinking is not a separate bug: `RouteLine` trims traveled geometry from `CarMarker`’s JS `onPositionChange` (playback still advances). The native icon is what stopped following.

## 3. Fix / remediation

Stop the per-frame Android rotation `setState` (once per 500 ms tick, last-known-good cadence). Every Android tick calls Fabric `setCoordinates` as well as the animator, and always re-syncs the React `coordinate` prop so a remount is not pinned to trip-start. Animator throws are caught so they cannot skip the command.

iOS path (`Marker.Animated` + `AnimatedRegion`) is unchanged.

## 4. Risk & impact on existing functionality

- **Blast radius:** `driver-app/components/CarMarker.tsx` (Drive tab + Android Auto `carSurface.tsx` via the same component) and `shared/components/CarMarker.tsx` (rider nearby-drivers / trip maps on Android). No backend, ride state machine, money, or insurance-period writes.
- **Rotation smoothness:** Android heading no longer interpolates between ticks (reverts the 2026-09-09 RAF tween). Corners may look a step less smooth; the car moving with the line is the priority.
- **Position smoothness:** `setCoordinates` is an instant `setPosition` and can cancel an in-flight `ObjectAnimator`, so Android motion may hop at the 500 ms tick instead of gliding. Still tracking, not frozen.
- iOS, GPS ingest, route snap, playback buffer: unchanged.

## 5. User-experience effect

Android drivers (and Android riders watching a moving car marker) see the icon follow the remaining polyline again. Visible mid-trip to anyone already in a ride after they pick up the JS (OTA on runtime 2.8.0) or a new AAB. No copy change. rider-app / driver-app have no visual-regression suite — reasoned about, not screenshotted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | `moveAndroidMarker` + once-per-tick rotation | Unfreeze the driver’s own icon |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Command + rotation contract tests | Prove `setCoordinates` runs even when the animator exists/throws |
| `shared/components/CarMarker.tsx` | Same Android move/rotation logic | Known fork — rider Android maps |
| `rider-app/__tests__/carMarkerPositionChange.test.tsx` | Comment only | Drop stale `animateAndroidRotationTo` reference |

## 7. Before / after

```
# Before (Android tick)
if (node?.animateMarkerToCoordinate) {
  node.animateMarkerToCoordinate(target, TICK_MS);
  resyncTimer = setTimeout(() => setAndroidCoord(target), TICK_MS);
} else {
  setAndroidCoord(target);
}
```

```
# After (Android tick)
moveAndroidMarker(node, target, TICK_MS); // animator + setCoordinates, both try/caught
setAndroidCoord(target);
```

## 8. Rollback plan

JS-only. OTA republish the previous 2.8.0 bundle, or a Play internal AAB from the prior commit. No DB, Stripe, or ride-state remediation. Does not require a remote flag.

## 9. Verification performed

- [x] Automated tests: `driver-app` `CarMarker.test.tsx` (run in this session)
- [ ] Manual repro on a physical Android driver phone (needs OTA or new AAB)
- [x] Blast-radius grep: `CarMarker`, `animateMarkerToCoordinate`, `setCoordinates`, `vehiclePosition` / `trimTraveled`
- [x] PIPEDA: no coordinates in new logs
- [ ] Feature-flagged: no — restoring a broken live map; a flag that left the freeze on would not be shippable. Rollback is OTA revert.

## 10. Sign-off

- [x] Rollback plan is concrete (OTA previous 2.8.0 JS)
- [x] Blast radius stated
- [x] UX field filled (Android marker follows again; heading steps per tick)
