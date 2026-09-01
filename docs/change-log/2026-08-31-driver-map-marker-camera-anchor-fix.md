# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`) |
| Related issue or gap ID | Live-testing report, 2026-08-31: "latency exists in the movement on the path, icon [is] missing" (screenshot: online, 46 km/h, no car icon visible) |

## 1. Issue / gap identified

On the driver dashboard map at driving speed, the car icon was sometimes not visible at all, and its movement felt laggy relative to the map.

## 2. Root cause

The follow-camera effect (`index.tsx`) anchored the camera's center on the **raw, undelayed** `location` GPS fix. `CarMarker`'s own icon, by design, renders `PLAYBACK_DELAY_MS` = 5 seconds **behind** that same fix (the playback-buffer smoothing technique, documented in `shared/utils/markerPlayback.ts`). On top of that, the camera then shifts its center a further ~18% of screen height **ahead** of its anchor point along the travel bearing ("pin the car low" framing).

Both offsets point the same direction relative to the icon's actual rendered position: the camera's anchor is already 5s (tens of meters at driving speed) ahead of where the icon is, and the "ahead" shift pushes the camera center even further past that. At 46 km/h this compounds to roughly 250–300m between the camera's center and the icon's actual position — enough, combined with map padding for the idle-state bottom panel, to push the icon very close to or outside the visible map area. This is the same class of bug the previous round's fix addressed for *bearing* (camera and icon disagreeing on rotation) — here it's *position* instead.

## 3. Fix / remediation

Extended the same "CarMarker is the single source of truth" pattern from the previous round to position:

- Added `onPositionChange?: (coordinate: TrackingLatLng) => void` to `CarMarker`, fired every playback tick with the exact position it just rendered (route-snapped, delay-applied) — same tick, same value the icon uses.
- `index.tsx`'s follow-camera effect now anchors its center (and the "ahead" pin-low offset) on that reported position instead of the raw `location` fix. The camera's ahead-offset is now always measured from where the icon actually is, so the two can no longer drift apart regardless of speed, GPS cadence, or playback delay. Falls back to the raw fix only before `CarMarker`'s first tick has reported a position (e.g. immediately after mount).

Deliberately did **not** reduce `PLAYBACK_DELAY_MS` itself in this pass — the idle ride state's GPS cadence (4s `timeInterval`) leaves only ~1s of margin against the current 5s delay; shrinking the delay further risks the playback buffer running dry more often (visible stutter/holding), which is a different regression than the one reported. Flagging this as a real, separate trade-off rather than silently tuning it — worth a follow-up only if the camera/icon-anchor fix above doesn't fully resolve the perceived "latency."

## 4. Risk & impact on existing functionality

- **Blast radius:** `driver-app/components/CarMarker.tsx` (new optional prop, additive, no other call site consumes it — rider-app has its own separate `CarMarker.tsx` copy, untouched) and the same follow-camera effect in `driver-app/app/driver/(tabs)/index.tsx` touched by the previous round's bearing fix.
- The zoom-tier calculation still reads the raw `location.coords.speed` (live ground speed), unchanged — only the *position* the camera centers on and shifts ahead from was moved to the marker's reported value.
- No ride-state, dispatch, fare, or backend-reachable path touched.

## 5. User-experience effect

- **Driver-facing.** The car icon should no longer disappear off the edge of the map while driving in course-up mode — the camera can't outrun it anymore, by construction. Whatever residual sense of "lag" remains (the icon trailing 5 seconds behind the driver's real-world position, not behind the app's own map) is the same shipped trade-off Uber/Lyft make for this technique — a softer, expected form of lag, not the app's map and icon visibly disagreeing with each other.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | Added `onPositionChange` prop, fired from the existing ticker alongside the position it applies to the marker | Expose the marker's actual rendered position as the camera's anchor |
| `driver-app/app/driver/(tabs)/index.tsx` | Follow-camera effect anchors on `CarMarker`'s reported position instead of the raw GPS fix | Prevent the camera and icon from drifting apart at speed |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Added a test asserting `onPositionChange` fires with the exact position the ticker renders that tick | Regression coverage |

## 7. Before / after

```tsx
// Before — camera anchors on the raw, undelayed GPS fix
let center = { latitude: c.latitude, longitude: c.longitude };
center = destinationPoint(center, mapHeading, aheadM); // ahead of the RAW fix
```
```tsx
// After — camera anchors on the icon's own (delayed) rendered position
let center = markerPosRef.current ?? { latitude: c.latitude, longitude: c.longitude };
center = destinationPoint(center, mapHeading, aheadM); // ahead of where the icon IS
```

## 8. Rollback plan

`git revert` — client-side only, no data touched, no migration.

## 9. Verification performed

- [x] `npx tsc --noEmit` (driver-app) — clean.
- [x] `npx eslint` on both changed source files — clean (0 errors, 0 warnings).
- [x] Full driver-app suite: **127/127 suites, 1435/1435 tests passing** (1434 baseline + 1 new).
- [x] New regression test: `onPositionChange` fires with the exact position the ticker applies that tick.
- [x] Blast-radius grep: confirmed no other call site consumes the new prop.
- [x] Worked through the geometry by hand (Web Mercator meters-per-pixel at the reported zoom tier, the 18% ahead-offset, the 5s delay at 46 km/h) to confirm the compounding-offset hypothesis is consistent with the screenshot (idle state, 46 km/h, icon not visible).
- [ ] Manual/on-device verification while actually driving — not performed; no device/emulator in this environment.

## 10. What was NOT verified

- No live-device confirmation that the icon is now reliably visible at speed — verified at the logic/geometry level (camera and icon can no longer diverge, by construction) but not observed live.
- Did not reduce `PLAYBACK_DELAY_MS` — flagged above as a real, separate trade-off against idle-state GPS cadence, not silently decided either way.
- rider-app's own `CarMarker.tsx` (a separate file) was not touched — out of scope, report was driver-app-specific.
