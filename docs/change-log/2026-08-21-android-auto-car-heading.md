# Change Impact & Risk Log — Android Auto car marker points the wrong way

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | srikumarimuddana@gmail.com (Claude Code) |
| Surface(s) | driver-app (Android Auto surface; the fix channel is also read by the phone's background location task) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/android-auto-earnings-privacy-2nzgpp` |
| Related issue or gap ID | Live-testing photos: car icon pointing north while driving west / southwest |

## 1. Issue / gap identified

On the head unit the car marker's direction did not match the direction of
travel. Two photos from a real drive show a north-up map with the icon pointing
roughly north while the driver travelled west on Victoria Ave and southwest on
Saskatchewan Dr.

## 2. Root cause

Two independent defects, both in the heading path:

1. **A carried bearing never expired** (`carFixChannel.carryHeading`). Only
   `watchPositionAsync` yields a course over ground; the car's one-shot paths
   (startup fix + the 3s staleness watchdog) do not, and on Android Auto those
   dominate because the phone is backgrounded in a cradle and Android throttles
   the watcher. `carryHeading` held the last real bearing with no age limit, so
   one early reading was republished for the whole drive — and because it was
   non-null it also suppressed `CarMarker`'s own derive-from-travel fallback.
   Nothing anywhere in the pipeline derived a course from movement.
2. **The marker was fed the camera's bearing**, not the true course
   (`carSurface.tsx`: `heading={cameraHeading ?? here.heading}`). `cameraHeading`
   is deliberately damped — it ignores turns under 4°, is null until the first
   course is observed, and freezes entirely while the driver has panned the map.
   Every one of those is correct for the camera and was silently freezing the
   icon as well.

## 3. Fix / remediation

- `resolveHeading(next, prev, prevHeadingAgeMs)` replaces `carryHeading` and
  states the priority explicitly: GPS course → bearing between consecutive
  fixes once the driver has moved `MIN_COURSE_MOVE_M` (10 m) → a carried bearing
  while younger than `CARRIED_HEADING_MAX_AGE_MS` (12 s) → null. It returns the
  source alongside the fix, so `adoptCarFix` restarts the bearing's clock only
  when a fix actually established one — a carried bearing ages instead of
  renewing itself on every watchdog tick.
- `bearingBetween()` added to the channel, so the surface and `CarMarker` no
  longer each guess a course from their own data.
- `carSurface.tsx` passes the marker `here.heading` — the world-space course.
  `flat` markers rotate relative to the map, so Google Maps already subtracts
  the camera bearing; the icon reads correctly in both north-up and course-up.
  `cameraHeading` now drives the camera only.

## 4. Risk & impact on existing functionality

Blast radius: **driver-app location display.** `carFixChannel` consumers,
grepped: `useCarLocation` (the car surface), `carLocationTask`,
`utils/backgroundLocation.ts` (the online driver's foreground service), and
`register.ts` (`getLastCarFix()` for the SOS payload).

- **SOS is position-only** — `register.ts` reads latitude/longitude and never
  the bearing, so nothing about the emergency payload changes. Verified by
  reading the call site.
- The shared `spinr_driver_last_location` cache stores lat/lng/at only; heading
  was never persisted, so no stored data changes shape.
- No ride state, dispatch, money or backend path is touched. Nothing is written
  to the database by this change.
- Behavioural risk worth naming: with the expiry in place a stationary driver
  whose watcher is starved now ends up with a **null** bearing after 12 s
  instead of a stale one. That is deliberate — `CarMarker` then falls back to
  its own travel bearing, and the map goes north-up — but it does mean the icon
  can settle to its last travel direction rather than a GPS course while parked.
  A parked car's heading is not meaningful either way.

## 5. User-experience effect

Driver-facing, visible mid-session to anyone connected to a head unit: the car
icon now turns with the vehicle, and the map rotates course-up once a course is
established (it could not before, because the same starved-bearing path fed the
camera). No rider-facing change; no phone-screen change (the phone dashboard has
a healthy watcher and was already correct).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/androidAuto/carFixChannel.ts` | `resolveHeading` + `bearingBetween`; carried bearings expire; `adoptCarFix` tracks when a bearing was established | A stale bearing was outliving every turn |
| `driver-app/lib/androidAuto/carSurface.tsx` | Marker takes the true course; debug fact reworded | Camera damping was freezing the icon |
| `driver-app/lib/androidAuto/__tests__/carFixChannel.test.ts` | Rewrote heading tests for the four branches; added expiry-under-watchdog-ticks and cardinal-bearing cases | Lock the regression down |

## 7. Before / after

```ts
// Before — carFixChannel.ts: carried forever, no derivation
export function carryHeading(next, prev) {
  if (hasHeading) return next;
  const carried = prev?.heading;
  return carried >= 0 ? { ...next, heading: carried } : next;   // never expires
}

// Before — carSurface.tsx
heading={cameraHeading ?? here.heading}   // damped + pan-frozen value
```

```ts
// After — GPS → derived from movement → recent carry → null
resolveHeading(next, prev, prevHeadingAgeMs): { fix, source }

// After — carSurface.tsx
heading={here.heading}                    // world-space course; the map subtracts its own bearing
```

## 8. Rollback plan

Display-only client code, no persisted data and no server component — reverting
the two source commits and shipping the next driver-app build restores the
previous behaviour exactly. No flag: the mechanism has no server-side switch,
and the change cannot produce a wrong position, price or ride state, only a
differently-rotated icon. Installed builds keep their current behaviour until
they update, so there is no half-state.

## 9. Verification performed

- [x] Automated tests — driver-app `lib/androidAuto`, `utils` and `__tests__`: **73 suites, 668 tests, all passing**, including 28 in `carFixChannel.test.ts` (rewritten) covering each branch, the watchdog-tick expiry, and cardinal bearings.
- [x] `tsc --noEmit` clean for driver-app.
- [x] Blast-radius grep — `carryHeading`, `resolveHeading`, `heading`, `getLastCarFix`, `adoptCarFix`, `publishCarFix` across `driver-app/`.
- [ ] Manual repro on hardware — not done (see below).
- [ ] Feature flag — no (see rollback).

## What was NOT verified

- **Nothing was run on a head unit or the DHU.** The diagnosis rests on reading
  the code against two photos; it was not reproduced live, and the fix has not
  been observed working. The photos are consistent with both defects, but a
  third cause (e.g. `Marker.Animated` dropping rotation updates on Android after
  `animateMarkerToCoordinate`) is not ruled out by these tests.
- The 10 m course threshold and the 12 s expiry are reasoned from road speed
  (10 m ≈ 0.5 s at 60 km/h; 12 s ≈ two watchdog cycles), not tuned against real
  GPS traces.
- The on-surface debug panel that would confirm which branch is driving the icon
  (`heading` / `mapHeading` facts) is still gated off when
  `EXPO_PUBLIC_ENV === 'production'`, so a production build cannot show it. Left
  as-is deliberately — opening it up is a separate decision.
- No production/`eas` build was run; no visual-regression tooling exists for
  this surface.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] UX field filled in for a behaviour change on an already-shipped screen
