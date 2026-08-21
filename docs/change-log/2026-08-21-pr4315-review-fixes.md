# Change Impact & Risk Log — PR #4315 review fixes

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | srikumarimuddana@gmail.com (Claude Code) |
| Surface(s) | driver-app (Android Auto), admin-dashboard (comment only), backend (test only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | PR #4315, branch `claude/android-auto-earnings-privacy-2nzgpp` |
| Related issue or gap ID | Self-review of PR #4315 (findings 1–5) |

## 1. Issue / gap identified

A manual review of this PR's own diff found five defects, two of them real
holes in the features it adds:

1. Earnings could be revealed on the idle screen and then **not re-hidden**: the
   eye button lives only on the no-route map-button strip, so once a ride
   started the total stayed on screen for the whole trip with the passenger
   aboard and no control to mask it.
2. `resolveHeading` derived a course from the previous fix with a distance guard
   but **no age guard on that fix** — a seeded (up to 60 s old, possibly the
   login position) or post-resume baseline produced the direction of the whole
   intervening journey, marked `'derived'` and trusted for 12 s.
3. The status bar's new lead number was the **booking-time** trip distance/ETA
   sitting where the fare used to be, which reads as "distance remaining".
4. A comment on the tracking page still described the teardrop pin and
   bottom-centre anchor this PR removed.
5. The backend's mirrored pin hexes had nothing to catch drift from the shared
   TS palette.

## 2. Root cause

1 and 3 are consequences of decisions made earlier in this PR that were not
followed through: hiding the toggle mid-ride (correct — the strip caps at 4)
without deciding what the *state* should do, and promoting a number to the hero
slot without checking what it measures. 2 is an incomplete guard: distance was
treated as sufficient evidence that a direction is a course. 4 and 5 are the
ordinary drift a diff leaves behind.

## 3. Fix / remediation

- `register.ts` re-masks earnings on any transition out of `idle`, logged.
  The reveal is now scoped to the screen the driver asked for it on.
- `resolveHeading` takes `prevFixAgeMs` and only derives when the baseline is
  younger than `MAX_COURSE_BASELINE_AGE_MS` (15 s). `adoptCarFix` passes
  `carFixAgeMs()`, which is `Infinity` for a seed — so a seed can never be a
  baseline.
- `useCarLiveRoute` now exposes the server-derived remaining `distanceKm`
  (it already carried `etaMinutes`), and `CarTripCard` leads with the live
  remaining distance/ETA, falling back to the ride's booking figures only when
  there is no trustworthy live route.
- Tracking-page comment corrected.
- `backend/tests/test_route_pin_palette_sync.py` parses `ROUTE_PIN_COLORS` out
  of the TS spec and asserts both the Static-Maps constants and the OSM
  fallback's inline hexes match it.

## 4. Risk & impact on existing functionality

Blast radius: **the Android Auto surface**, plus one comment and one new test.

- `useCarLiveRoute`'s return type gained a field; its only consumer is
  `carSurface.tsx` (grepped). `liveRouteShared` already published `distanceKm`,
  so no fetch or poll behaviour changes.
- `CarTripCard`'s new props are optional and default to null, so the completed-leg
  path and any caller that omits them behave exactly as before.
- `resolveHeading` gained a parameter with a default of `0`; the only production
  caller passes the real age. Existing call sites cannot silently change meaning.
- The re-mask runs inside the existing `key !== lastKey` chrome block, so it
  fires once per state transition, not per render.
- No money, ride-state, dispatch, insurance-period, or backend runtime path is
  touched. The new backend file is a test.

## 5. User-experience effect

Driver-facing, visible mid-session:

- Revealed earnings are hidden again as soon as a ride starts. A driver who
  wants the total during a ride cannot get it — deliberate, and the trade the
  privacy feature exists to make. They can reveal again on the completed screen.
- Mid-ride the bar now counts **down** the remaining kilometres/minutes to the
  current destination instead of restating the booked trip length.
- No rider-facing change; the tracking page is comment-only here.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/lib/androidAuto/register.ts` | Re-mask on leaving idle | Reveal outlived its screen |
| `driver-app/lib/androidAuto/carFixChannel.ts` | `MAX_COURSE_BASELINE_AGE_MS` + `prevFixAgeMs` guard | Derived a course across a stale baseline |
| `driver-app/lib/androidAuto/useCarLiveRoute.ts` | Expose remaining `distanceKm` | The surface had no remaining distance |
| `driver-app/lib/androidAuto/CarTripCard.tsx` | Lead with live remaining km/ETA | Booking figures read as remaining |
| `driver-app/lib/androidAuto/carSurface.tsx` | Pass the live remaining values | — |
| `admin-dashboard/src/app/track/[rideId]/page.tsx` | Corrected stale comment | Described a removed teardrop |
| `backend/tests/test_route_pin_palette_sync.py` | New | Nothing caught palette drift |
| `driver-app/lib/androidAuto/__tests__/{carFixChannel,register}.test.ts` | 3 new cases | Lock each fix down |

## 7. Before / after

```ts
// Before — a reveal survived into the ride, with the toggle gone from the strip
if (key !== lastKey) { lastKey = key; /* chrome only */ }

// After
if (rideState !== 'idle' && !useCarEarningsPrivacy.getState().hidden) {
  useCarEarningsPrivacy.getState().reset();
}
```

```ts
// Before — distance alone made a direction a course
if (prev && metresBetween(prev, next) >= MIN_COURSE_MOVE_M) { …'derived' }

// After — the baseline has to be recent too
if (prev && prevFixAgeMs <= MAX_COURSE_BASELINE_AGE_MS &&
    metresBetween(prev, next) >= MIN_COURSE_MOVE_M) { …'derived' }
```

## 8. Rollback plan

Display-only client code plus one test; nothing persisted, no server component.
Reverting this commit restores the previous behaviour, and the apps only change
on their next build. Same reasoning as the three earlier entries on this branch.

## 9. Verification performed

- [x] driver-app: **73 suites / 671 tests pass**, incl. 30 in `carFixChannel.test.ts` (2 new: stale baseline, seeded baseline) and the new `register.test.ts` re-mask case; `tsc --noEmit` clean; eslint clean on the touched files (one pre-existing unused-import warning).
- [x] admin-dashboard: 35 files / 339 tests pass, `tsc --noEmit` clean, **`npm run build` run and passing**.
- [x] backend: `test_utils_extended.py` + `test_route_snapshot_coverage.py` + the new palette-sync test — 251 passed, 1 skipped.
- [x] Blast-radius grep — `useCarLiveRoute`, `CarTripCard`, `resolveHeading`, `useCarEarningsPrivacy`, `carFixAgeMs`.
- [ ] Manual/hardware check — not done.

## What was NOT verified

- **Still nothing rendered on a head unit or the DHU.** The re-mask, the
  countdown numbers and the heading behaviour are all covered by unit tests
  against mocks, not observed in a car.
- `MAX_COURSE_BASELINE_AGE_MS` (15 s) is reasoned from the watchdog cadence, not
  measured against real GPS traces — same caveat as the 10 m / 12 s constants.
- The live remaining distance depends on the OSRM-backed live route; when it is
  absent the bar silently falls back to the booking figures, and how often that
  happens in the field is unmeasured.
- The palette-sync test parses the TS file with a regex; a future refactor of
  `ROUTE_PIN_COLORS` into a different shape would make it fail loudly (asserted)
  rather than silently pass, but it is still source parsing, not a real import.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] UX field filled in for behaviour changes to an already-shipped screen
