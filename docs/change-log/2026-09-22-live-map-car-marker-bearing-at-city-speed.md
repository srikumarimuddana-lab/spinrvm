# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Author | Claude Code session (branch `claude/quirky-bardeen-8cci1d`) |
| Surface(s) | rider-app, driver-app (both via `shared/`) |
| Domain (Sentry tag) | rides |
| PR / commit link | _pending_ |
| Related issue or gap ID | Live-testing report: "in live map the car marker is supposed to go as per the direction — the bearing is not properly aligned in the live map". No prior `ACTION_ITEMS.md` entry (grepped `bearing`/`rotat`/`sideways`/`misalign` — this is a fresh report, not a known-open item). |

## 1. Issue / gap identified

On the live tracking maps, the car icon's **position** sits correctly on the road
but its **heading** points across the street rather than along it. The
misalignment is speed-dependent: it clears up at highway speed and is worst in
stop-and-go traffic, creeping through a turn, and on the last block before
pickup — exactly the moments a rider is watching the map.

## 2. Root cause

`selectBearing()` in `shared/utils/vehicleTracking.ts` gated the **route-segment**
bearing branch on the same `minMoveMeters` floor as the **travel** bearing branch:

```ts
if (snap && movedMeters >= minMoveMeters) { ... return { source: 'route' } }
```

Those two branches need that floor for opposite reasons, and conflating them is
the bug:

- The **travel** bearing is measured from `from`→`to`. A sub-3 m chord there is
  GPS noise, so the floor is correct and necessary.
- The **route** bearing is the direction of the road segment the fix snapped
  onto. That is a property of the road geometry. It is exactly as well-known at
  5 km/h as at 50, and needs no displacement at all.

`CarMarker`'s playback ticker runs at `TICK_MS = 500`, so `MIN_BEARING_MOVE_M = 3`
is a **21.6 km/h** threshold. Below it the route branch was simply unreachable.
`selectBearing` then fell through to `{bearing: null, source: 'none'}`, and
`coalescePlaybackBearing` supplied the **raw-GPS Catmull-Rom spline tangent**
instead. Meanwhile the ticker snaps the rendered *position* to the route
unconditionally (`snapToRoute` runs before `selectBearing`). Hence the exact
observed signature: on-road position, noise-driven heading.

This also explains the speed dependence — above ~21.6 km/h the route branch
cleared the floor and the icon was correct, which is why the defect survived
earlier bearing work that was debugged at driving speed.

**There is a second, worse tier below that.** `markerPlayback.playbackPosition`
only produces a spline/linear bearing when the bracketing segment itself clears
`MIN_SEGMENT_MOVE_M = 2 m`. Executed against the real module: a car crawling at
~3.6 km/h yields `{bearing: null, mode: 'interpolating'}`, and a car stopped at
a light yields the same. So at the low end the old code had **no bearing source
available at all** — not the route (under the 3 m floor), not the spline (under
the 2 m segment floor), and not the reported heading (shut out by the
`hasMovementBearing` latch, correctly). The icon simply **froze at its last
heading**, which immediately after a turn is the *pre-turn* heading. That is the
sharpest form of the reported symptom: a car that has turned onto a cross street
and is still displayed pointing the old way.

The cleanest framing: `CarMarker`'s own docstring states the bearing priority as
"route-segment direction → direction of travel → the reported GPS heading". That
contract was silently **not honoured below 21.6 km/h** — the first tier was
unreachable and, below ~7 km/h, the second was too. This change makes the code
match the contract it already documents, at all speeds, rather than introducing
a new rule.

## 3. Fix / remediation

Added an opt-in `movementConfirmed?: boolean` parameter to `selectBearing()`
that frees **only** the route branch from the movement floor. Both `CarMarker`
tickers pass `p.mode === 'interpolating' || p.mode === 'extrapolating'` — the
same "the chord is short but the car is really moving" signal
`coalescePlaybackBearing` already trusts to promote its spline tangent. If that
signal is good enough to trust a *derived* tangent, it is good enough to trust
the *road's own* direction, which is strictly better evidence.

The travel branch keeps its floor unchanged. The parameter defaults to `false`,
so any caller that omits it behaves exactly as before.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface but narrow and fully enumerated.**

- `selectBearing` has exactly **two** production consumers, both updated in this
  commit: `shared/components/CarMarker.tsx` (rider-app: `ride-in-progress`,
  `driver-arriving`, `driver-arrived`, `(tabs)/index`, `ride-options`) and
  `driver-app/components/CarMarker.tsx` (driver map + Android Auto).
  Verified by `grep -rn "selectBearing"` across all `.ts`/`.tsx`, excluding
  `node_modules`.
- Other importers of `shared/utils/vehicleTracking` (`RouteLine.tsx`,
  `navigationSteps.ts`, `gpsSmoothing.ts`, `markerPlayback.ts`,
  `markerRouteTracking.ts`, `useVisibleHeatmapCells.ts`,
  `driver/(tabs)/index.tsx`) import only `distanceMeters` / `snapToRoute` /
  `destinationPoint` / `bearingDegrees` — none touches `selectBearing`.
- **This is a marker-rendering change only.** It does not read or write
  `rides.status`, emit or consume a WebSocket event, touch
  `driver_insurance_periods`, or reach any money/wallet path. No background loop
  in `core/lifespan.py` is involved. No backend file changed.
- **No prop-surface change**, so `shared/components/__tests__/CarMarkerParity.test.ts`
  (which diffs the two `CarMarkerProps` interfaces) is unaffected — I added a
  function parameter, not a component prop.
- **Fork parity honoured**: both sides of the `CarMarker` fork registered in
  `docs/known-forks.md` are changed in this one commit, which is precisely the
  one-way-port failure that registry exists to prevent.
- **Regression surface is provably empty for `snap === null`.** The changed
  condition can only alter behaviour when `snap` is non-null, which requires a
  caller to pass `routeCoordinates`. Grepping every test file for
  `routeCoordinates` yields exactly two component-test call sites
  (`driver-app/__tests__/components/CarMarker.test.tsx:387,415`), both traced by
  hand below. Every other `CarMarker` test — including the two that deliberately
  pin sub-threshold behaviour
  (`driver-app/__tests__/components/CarMarker.test.tsx` ~737 and
  `rider-app/__tests__/carMarkerPositionChange.test.tsx` ~503) — passes no route
  and uses `mode: 'waiting'`, so it is a strict no-op for them on both counts.
- The two route-bearing tests traced: on the first tick the route segment
  bearing is `0` (due-north polyline), identical to the playback bearing that
  previously won, so the assertion value is unchanged; on the second tick
  `movedMeters ≈ 4 m` already cleared the floor before this change, so that tick
  is untouched.

**CORRECTED after adversarial review — a stopped car does NOT get a route
bearing.** The first version of this change computed `movementConfirmed` from
`p.mode` alone. That was wrong, and the review caught it: `playbackPosition`
reports `mode: 'interpolating'` with a **null bearing** for a stationary car
(verified: `speedMps: 0.14`, `bearing: null`), because elapsed time — not
displacement — is what puts render time between two fixes. Mode alone would
therefore have confirmed "movement" with zero GPS-corroborated displacement.

The failure that makes it a blocker rather than a nicety: a car stopped **at a
turn**, where the pre-turn and post-turn segments are both inside the forward
search window and both within `MAX_ROUTE_SNAP_M`. `snapToRoute`'s own module
doc already notes that the continuity hint is a forward bias, not a
turn-disambiguator, so the bearing could flap between the two **every tick for
as long as the car sat there** — an unbounded-duration glitch, not the one-tick
blip the ≥ 3 m path already risks. On driver-app it would be worse still: that
fork feeds `onBearingChange`, which drives the **course-up camera**, so the
whole map would rock back and forth while the driver is stationary.

The gate is now `(p.mode === 'interpolating' && p.bearing != null) ||
p.mode === 'extrapolating'` — exactly `coalescePlaybackBearing`'s own
condition. `extrapolating` needs no guard: that branch always returns a
computed bearing, and a null one falls through to `'holding'` instead
(verified in `markerPlayback.ts`).

Measured against the real module, this keeps the fix and closes the hole:

| Speed (2 s fix cadence) | mode | bearing | `movementConfirmed` |
|---|---|---|---|
| stopped (0.3 m jitter) | interpolating | null | **no** — holds last heading |
| ~3.6 km/h (walking) | interpolating | null | **no** — holds last heading |
| ~9 km/h | interpolating | 90 | yes — route bearing |
| ~15 km/h | interpolating | 90 | yes — route bearing |
| ~30 km/h | interpolating | 90 | yes — route bearing |

The whole originally-broken band is still fixed. What is given up is below
~3.6 km/h, where displacement is genuinely under the GPS noise floor and
holding the last heading is the honest answer rather than a regression.

A second, related finding from the same review is closed by the same gate: the
`hasMovementBearingRef` latch could previously arm at **trip start, before the
vehicle had moved at all** (any ride phase that loads a route while the car is
stationary within 35 m of it), permanently shutting out the raw-heading
cold-start fallback for that mount. With the gate, a never-moved car never
reaches the route branch, so the latch cannot arm early.

**What could still regress, honestly stated:** a car that is *barely* moving
while snapped to a wrong-but-nearby segment (opposing carriageway, out-and-back
street, a crossing street at an intersection) will now take that segment's
bearing where it previously took the spline tangent. Three existing guards
already bound this, and none is weakened here: the
`lastRouteSegmentIndexRef` continuity hint, `MAX_ROUTE_SNAP_M = 35`, and the
`courseReference` veto. It is also the same exposure the ≥ 3 m path has always
accepted at higher speed — this change does not create the risk class, it
extends an existing one to lower speeds, where a wrong segment is *less* likely
because the car has moved less far from the last confirmed one.

## 5. User-experience effect

- **Rider:** visible. The driver's car icon now points along the road it is
  driving during slow/city stretches instead of wobbling across it. This is a
  correction of an existing wrong behaviour, not a new feature.
- **Driver:** visible on the driver map and Android Auto surface, same
  correction.
- **Visible mid-session: yes** — a rider already tracking a driver, or a driver
  already online, will see it on the next app build. There is no server
  component, so nothing changes for anyone already running an installed build
  until they update.
- No copy, notification, or ride-state change. Nothing corporate/admin-facing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/vehicleTracking.ts` | Added opt-in `movementConfirmed?: boolean` to `selectBearing`; route branch now clears on `movedMeters >= minMoveMeters \|\| movementConfirmed` | The road's direction needs no displacement to be meaningful; only the travel branch does |
| `shared/components/CarMarker.tsx` | Ticker computes `movementConfirmed` from playback mode and passes it | Rider-app live maps |
| `driver-app/components/CarMarker.tsx` | Identical change | Fork parity (`docs/known-forks.md`) — a one-way port here is the exact failure that registry exists to catch |
| `rider-app/__tests__/vehicleTracking.test.ts` | New `describe` block, 5 cases | Pin the fix, the unchanged default, and that the veto is not bypassable through the new door |

## 7. Before / after

```ts
// Before — the road's direction was unreachable below 21.6 km/h
if (snap && movedMeters >= minMoveMeters) {
  if (!contradictsReference(snap.bearing)) {
    return { bearing: snap.bearing, source: 'route' };
  }
  refusedMovementBearing = true;
}
```

```ts
// After — either real movement this tick, or a caller-confirmed one
if (snap && (movedMeters >= minMoveMeters || movementConfirmed)) {
  if (!contradictsReference(snap.bearing)) {
    return { bearing: snap.bearing, source: 'route' };
  }
  refusedMovementBearing = true;
}
```

Concrete scenario — car doing ~15 km/h (2.1 m per 500 ms tick) on a westbound
street, snapped to the route, Android reporting its placeholder heading `0`:

| | Bearing source | Rendered heading |
|---|---|---|
| Before | `none` → spline tangent via `coalescePlaybackBearing` | raw-GPS tangent (noise-dominated at this speed) |
| After | `route` | `270` — along the street |

Both rows are executed and asserted in the verification harness below.

## 8. Rollback plan

**No flag, no migration, no data to remediate — and none is needed.** This is
client-only rendering code with no persisted state: nothing is written to any
table, no ride state is touched, no money moves. A `git revert` of this single
commit is a complete and sufficient rollback, which is the exception
`CLAUDE.md` gate 7 allows for a genuinely isolated change.

Because it ships inside a mobile build, the practical rollback is: revert the
commit and cut the next build. **There is no way to turn this off on an
already-installed build**, which is the honest limitation of any client-side
marker change and is why the mitigation is the narrow default (`false`)
rather than a runtime switch.

**Revert the whole commit, not one file.** Reverting
`shared/utils/vehicleTracking.ts` on its own would leave the two `CarMarker`
call sites passing a property the parameter type no longer declares — inert at
runtime (JS ignores extra object properties), but a TypeScript excess-property
**compile error**, so the build would fail rather than degrade. The smallest
safe undo is `git revert` of the full commit. Alternatively, a one-line
in-place kill switch is available without touching the call sites: change the
route-branch condition back to `movedMeters >= minMoveMeters` and leave the
parameter declared but unread.

## 9. Verification performed

- [x] **Automated tests written** — 5 new unit cases in
      `rider-app/__tests__/vehicleTracking.test.ts`.
- [ ] **Jest NOT run in this environment.** `npm install` fails with
      `403 Forbidden` on `registry.npmjs.org`; the session's agent proxy
      confirms a gateway policy denial for that host
      (`connect_rejected` on `registry.npmjs.org:443` and
      `registry.yarnpkg.com:443`). No `node_modules` exists in any workspace,
      so the jest suites could not be executed here and **must be run in CI**.
- [x] **Bug reproduced against SHIPPED code.** The `git HEAD` copy of
      `shared/utils/vehicleTracking.ts` was extracted and executed directly on
      the reported scenario (westbound street, snapped to route, Android
      placeholder `heading: 0`):

      | Speed | chord / 500 ms tick | `selectBearing` result on shipped code |
      |---|---|---|
      | ~15 km/h | 2.1 m | `{"bearing": null, "source": "none"}` — route bearing 270 unreachable |
      | ~30 km/h | 4.2 m | `{"bearing": 270, "source": "route"}` — correct |

      This confirms the defect and its speed dependence in production code, and
      explains why it survived earlier bearing work debugged at driving speed.
- [x] **Compensating execution of the real source.** `tsc` is available
      globally, so `shared/utils/vehicleTracking.ts` was compiled and executed
      directly (it is pure math with no imports) against a 20-assertion harness:
      all 13 pre-existing `selectBearing` assertions transcribed from the jest
      suite (proving no regression), the 5 new ones, and a 2-assertion
      before/after repro of the reported bug. **20 passed, 0 failed.** This
      executes the real shipped function, not a transcription of it — but it is
      not jest, and it does not exercise the `CarMarker` React components.
- [x] **Typecheck** — `tsc --noEmit --strict` clean on
      `shared/utils/vehicleTracking.ts`. The two `CarMarker.tsx` files could not
      be typechecked (they import `react-native`/`expo-image`, absent without
      `node_modules`).
- [x] **Blast-radius grep** — `selectBearing` (all consumers), importers of
      `vehicleTracking`, `routeCoordinates` across all test files,
      `visualRotationDegrees`, and all `CarMarker` call sites. Listed in §4.
- [x] **Reviewed against `CLAUDE.md` conventions** — no ride-state transition,
      no money arithmetic, no RLS/PIPEDA surface (no coordinates are logged by
      this change), no new background loop, no prop-surface change so the
      CarMarker parity guard is untouched, and both fork sides changed together
      per `docs/known-forks.md`.
- [x] **Adversarial review** — `spinr-edge-case-reviewer` run against the actual
      diff, per `CLAUDE.md` gate 10. It returned **FIX BLOCKERS** and was
      right: the `movementConfirmed` derivation checked `p.mode` without
      `p.bearing`. Fixed in a follow-up commit (see the CORRECTED subsection in
      §4), with the gate's speed behaviour re-measured against the real
      `markerPlayback` and the load-bearing property pinned by two new tests in
      `rider-app/__tests__/markerPlayback.test.ts`. **Disclosure:** the review
      landed after the first commit was already pushed, so the blocker was live
      on the branch briefly; it was never merged.
- [x] **Not feature-flagged, justified:** rider-app/driver-app have no
      `app_settings`-style runtime flag plumbed to this component, the change is
      a correction of wrong behaviour rather than new UX, and it is inert by
      default at the library boundary.

## 10. What was NOT verified

- **No jest run, in either app.** Blocked by the npm-registry denial above, not
  by choice. CI must be green before merge; the component-level behaviour
  (`CarMarker` ticker wiring) rests on hand-tracing plus the harness, not on an
  executed component test.
- **No device or simulator run.** Neither rider-app nor driver-app has any
  visual-regression tooling at all (per `CLAUDE.md` gate 6), so this marker
  change was **reasoned about and unit-verified, not screenshotted**. The visual
  claim "the icon now points along the road" is an inference from the bearing
  value, not an observed frame. No admin-dashboard page was touched, so the
  merge-blocking Playwright visual job is not involved.
- **No real GPS trace replayed.** The harness feeds synthetic snap results. The
  speed-dependence claim (21.6 km/h) is arithmetic from `TICK_MS`/
  `MIN_BEARING_MOVE_M`, not a measurement from a recorded ride.
- **The iOS map-heading gap is NOT addressed here** — see the separate finding
  below. This change does not fix it and does not make it worse.

## 11. Separate finding, deliberately out of scope

While tracing this, a **second, independent** bearing defect was found and is
**not** fixed by this commit:

`shared/utils/vehicleTracking.ts` exports `visualRotationDegrees(worldBearing,
mapHeading)` — "world-space course minus the map camera heading" — for
screen-upright Apple Maps annotations. It is applied **only** in
`driver-app/components/CarMarker.tsx` (lines ~717, ~774). The shared
`CarMarker` that rider-app uses applies the raw world bearing directly as a
screen-space view transform (`iosRotateStyle`) with no map-heading subtraction.

On iOS the rider maps use Apple Maps (`provider={Platform.OS === 'android' ?
PROVIDER_GOOGLE : undefined}`), where `Marker.rotation` is a no-op — which is
why the view transform exists. `rotateEnabled` is left at its react-native-maps
default of `true` on the live-tracking screens (`ride-in-progress.tsx`,
`driver-arriving.tsx`, `driver-arrived.tsx`), though it *is* explicitly
`false` on the static receipt maps (`ride-details.tsx`, `ride-completed.tsx`).
So a rider who rotates the map — deliberately, or accidentally while
pinch-zooming — leaves the car icon wrong by exactly the map's rotation angle
until they straighten it. Android is unaffected: a `flat` Marker's rotation is
world-space and Google Maps subtracts the camera bearing itself.

This is the same bug class `driver-app` diagnosed and fixed for itself
(documented verbatim at `driver-app/app/driver/(tabs)/index.tsx` ~985–992) and
is another instance of the one-way-port gap `docs/known-forks.md` exists to
catch. It is left out of this commit deliberately: the two candidate fixes
(`rotateEnabled={false}` on the three rider live maps, enforcing the north-up
convention the registry already documents as correct for rider-side; versus
porting `mapHeadingRef` plumbing into the shared component and every rider map
screen) are a different logical change with a different blast radius and a
real UX question attached, and `CLAUDE.md`'s batch-size rule keeps them apart.
**Recommend `rotateEnabled={false}`** — it makes the north-up invariant the
shared component already assumes true by construction, in three lines, with no
async camera reads.
