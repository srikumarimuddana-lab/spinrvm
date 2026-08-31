# 2026-08-30 — Course-up camera through the whole drive, not just idle

Round 6, direct follow-up to the same day's PR #4715 (stop-drift + idle
panel overlap). User explicitly asked that the car/route always orient
bottom-to-top on screen; confirmed rider app stays north-up (unchanged,
matches Uber/Lyft/Gojek/Grab's rider-vs-driver convention from round 3's
research) and asked to extend course-up to `navigating_to_pickup` and
`trip_in_progress`. Branch `claude/map-vehicle-tracking-animation-3e85y2`,
restarted from main after #4715 merged.

## 1. Issue/gap identified

Course-up rotation (round 3) only ran while online-idle (cruising with no
ride). The moment a ride started, the camera dropped to a one-time
route-overview `fitToCoordinates` fit with no ongoing rotation — so
bottom-to-top framing was missing for the two states where a driver is
actually navigating: driving to pickup and the trip itself.

## 2. Root cause

The follow-camera effect added in round 3 was scoped `rideState !== 'idle'
→ return`, deliberately deferred at the time to keep that PR isolated;
active-ride phases kept a static overview instead.

## 3. Fix/remediation

- Extended the existing follow-camera effect (course-up rotation +
  speed-adaptive zoom — unchanged logic, already covered by round 3-5's
  fixes) to also run during `navigating_to_pickup` and `trip_in_progress`,
  via a new `COURSE_UP_RIDE_STATES` set. `arrived_at_pickup` (stationary,
  PIN entry) and the brief `ride_offered`/`trip_completed` states are
  deliberately excluded — no ongoing travel direction to orient toward.
- This reintroduces the exact class of bug fixed for the idle panel earlier
  the same day (car hidden behind bottom UI), this time under
  `ActiveRidePanel`'s bigger **draggable** sheet. Fixed the same way, one
  level more carefully since the sheet's height varies: `ActiveRidePanel`
  now reports its open/collapsed state via a new `onExpandedChange`
  callback (fires off the sheet's own already-tracked `isCollapsed` state),
  and the map's `mapPadding` tracks it — expanded padding uses the exact
  same `0.65 * windowHeight` cap `ActiveRidePanel` itself already enforces
  (a real bound, not a guess), collapsed-peek padding is a documented
  estimate of the drag-handle + header row.

## 4. Risk & impact on existing functionality

- `ActiveRidePanel` gains one new optional prop (`onExpandedChange`) with
  no default-path behavior change — every existing consumer (only
  `index.tsx`) and every existing test is unaffected unless the prop is
  passed.
- The follow-camera effect's own logic (bearing/zoom/rotation math) is
  unchanged — only which ride states it runs in. No change to the ride
  state machine, dispatch, fare, or the rider app.
- `mapPadding`'s active-ride branch is coupled to `ActiveRidePanel`'s
  `maxPanelHeight` formula (`0.65` for these two states) — if that fraction
  is ever changed there, this constant must follow it (called out in both
  files' comments).

## 5. User experience effect

Driver-facing: the map now rotates bottom-to-top for the entire drive
(navigating to pickup and during the trip), not just while cruising
online. Rider app unchanged (still north-up).

## 6. Files modified

| File | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/index.tsx` | follow-camera effect scope widened; mapPadding tracks ActiveRidePanel | bottom-to-top through the whole drive, no marker-under-panel regression |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | new `onExpandedChange` prop | lets the map track the sheet's real height instead of guessing |
| `driver-app/__tests__/components/ActiveRidePanel.test.tsx` | new test pinning the callback fires `true` on mount | pin the new contract |

## 7. Rollback plan

JS-only, `git revert` + automatic OTA republish; no native build, no
migration, no live data touched.

## 8. Verification performed

- `tsc --noEmit` clean; eslint clean on all three changed files.
- Full driver-app suite: 1329/1329 (117 suites), up from round 5's 1324
  (5 new tests: 1 new + this round's existing suites picking up the
  callback wiring implicitly via unchanged prior tests still passing).
- Rider-app untouched this round — no rider files in the diff, prior round's
  1893/1893 stands.

## 9. What was NOT verified

- No on-device testing — the mapPadding-tracks-sheet-height approach for a
  **draggable** sheet is the least-tested piece of this whole map-tracking
  effort; the collapsed-peek height constant (110dp) is an estimate like
  the idle panel's, not measured. If it proves off, it's a one-line tune.
- The interaction between a driver dragging the sheet mid-drive and the
  follow camera re-centering hasn't been observed — mapPadding only updates
  on SETTLE (collapsed vs. expanded), not continuously during the drag
  gesture itself, by design (avoiding a per-frame Animated listener).
