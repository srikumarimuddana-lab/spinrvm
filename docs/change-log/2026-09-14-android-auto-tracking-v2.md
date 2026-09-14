# Android Auto marker position and direction — Change Impact Log

Date: 2026-09-14. Author: Codex. Domain: drivers. Branch: `codex/android-auto-heading-road-fix`.

## Issue and evidence

User reports east/west sideways motion and northbound travel with the vehicle facing south, including active-trip photos. Current-code diagnostics reproduced a south-directed route overriding northbound movement, and an old fix rewinding the location channel. The photographs do not identify the installed APK/OTA revision.

## Execution checklist

The user requested coding only and will perform device testing to conserve usage. No broad test suites or native builds are run during this implementation. Local static validation and focused review remain necessary. Each code batch is committed before the next starts; this checklist is the task tracker because TodoWrite is unavailable.

- [x] Foreground measurement metadata and chronological acceptance (`a6835daa9`).
- [x] Background producer metadata (`8b7d589ea`).
- [x] Direction-aware matching and route animation geometry (`74d50137f`).
- [x] Marker metadata props: both forks forward accuracy and reject duplicate/older fixes before changing smoothing state when opted in.
- [x] **Course-reference veto, wired end-to-end to the head unit** (this entry, 2026-09-14, second session).
- [ ] Android Auto camera and playback route continuity (plan Tasks 5 and 7).
- [ ] Route response ordering and final static review (plan Task 8).

### Correction to the diagnosis above

The driver confirmed on 2026-09-14 that **the map does rotate correctly** — travel is
always up the screen, i.e. the course-up camera is working. That single fact makes the
reported symptoms solvable rather than ambiguous. On a course-up map the icon's on-screen
angle is `(marker world bearing − travel bearing)`, so the three reports resolve to one
number:

| Travel | Reported icon | Implied marker world bearing |
|---|---|---|
| North (0°) | points south | 180° |
| East (90°) | horizontal | 180° |
| West (270°) | horizontal | 180° |
| South (180°) | correct | 180° |

The marker's bearing was pinned near south regardless of travel, while the camera followed
a *different and correct* source. That is the actual defect: `carSurface.tsx` feeds the
camera `here.heading` (the car channel's resolved course) and the driver's own report
proves that value is good — but `selectBearing` never uses it. It returns `snap.bearing`
(route-segment geometry) whenever the marker moved past the 3 m floor, and once
`hasMovementBearingRef` latches it ignores the reported heading entirely. A known-good
compass course was sitting unused in `headingRef` while the icon followed route geometry
pointing the other way.

Note what this corrects: the earlier entry recorded that no icon rotation is applied
because "rotating the bitmap by 180 degrees would also reverse currently correct southbound
travel". That reasoning holds and the conclusion was right — but the underlying cause is
not a bitmap offset at all, and the car art (`driver-app/assets/images/car_marker.png`)
was confirmed to point north at rotation 0.

### Why the previous batch changed nothing on the head unit

Commits `a6835daa9` / `8b7d589ea` are live. `74d50137f` and the marker prop work were
**unreachable**: `travelBearing` on `snapToRoute` is an optional parameter no caller passed,
`snapToRouteDirectional` in `markerRouteTracking.ts` had zero call sites, and nothing
passed `trackingV2` / `fixAccuracyM`. A build from that state would have behaved exactly as
the photographs show. `trackingV2` remains deliberately unwired (opt-in until device
acceptance); the veto below is wired, because it is inert by construction when no
corroborated course exists.

## Changes and blast radius

| Files | Change | Effect / risk |
|---|---|---|
| `driver-app/lib/androidAuto/carFixChannel.ts`, `useCarLocation.ts` | Retain native capture time, accuracy and speed; reject stale/older/invalid display updates; bound watchdog concurrency | All car display producers share the channel. `register.ts` also reads its last fix for SOS; rejected display fixes leave the last valid position available. Timestamped cache entries retain capture time. |
| `driver-app/lib/androidAuto/carLocationTask.ts`, `driver-app/utils/backgroundLocation.ts` | Preserve capture metadata for headless fixes; choose latest car-task sample by timestamp | Prevents old trip batches from reversing the car display; raw recording still happens before the display gate. |
| `shared/utils/vehicleTracking.ts`, `shared/utils/markerRouteTracking.ts` | Optional travel-direction constraint and bounded road-vertex animation steps | Existing `snapToRoute` callers (both markers, shared RouteLine, dashboard deviation checks) keep prior behavior unless a movement constraint is supplied. |
| `shared/utils/vehicleTracking.ts` | `selectBearing` gains optional `courseReference` / `maxCourseErrorDeg`; new `'reference'` `BearingSource`; `coalescePlaybackBearing` passes a `reference` selection through untouched | **Shared with live rider-app and the driver phone app.** Both new parameters are optional and both default to today's exact behaviour — with `courseReference` omitted the veto predicate is constant-false, so every branch returns what it returned before. Consumers of `BearingSource` are only the two `CarMarker` forks and this module; both narrow with `===` and neither switches exhaustively, so the added union member cannot change their control flow. Proven by a dedicated "leaves default callers byte-identical" test. |
| `driver-app/components/CarMarker.tsx`, `shared/components/CarMarker.tsx` | New optional `courseReference` prop forwarded into `selectBearing`; added to `_propsAreEqual` and to the ticker's options ref | Both forks kept in parity (`docs/known-forks.md`). rider-app passes it nowhere, so the shared fork is unchanged in practice. `CarMarkerParity.test.ts`'s `COMMON_PROPS` updated in the same change, as that file's own header requires. |
| `driver-app/lib/androidAuto/carSurface.tsx` | Computes the corroborated course (`gps`/`derived` only, never `carried`/`none`) and passes it to `CarMarker`; adds a `courseRef` debug fact; replaces the hand-written lazy-import prop shape with a type-only reference to the real component | This is the wiring that was missing — without it nothing in this branch reaches the head unit. The type-only reference is erased at compile time (same pattern as `mapRef`) so it cannot defeat the lazy `require`; it also removes the class of bug where the narrow three-prop literal made every other marker prop a compile error rather than a feature. |
| `driver-app/lib/androidAuto/carLocationTask.ts` | An undated batch is still dropped, but now logs why | Addresses the *silence* introduced by `8b7d589ea`, not the drop. A first attempt restored a fallback to array position; adversarial review killed it on two verified counts. (1) `carFixChannel.ts:270` already refuses an undated fix whenever `lastFix.timestampMs` is set, so the fallback bought nothing past the cold-boot window — the original claim here overstated its reach. (2) `locationIntegrity.ts:83,93` reads `loc.timestamp` unguarded, so an undated sample makes `elapsed` NaN: the teleport check is skipped for that sample *and* the poisoned baseline blinds the next one. Handing an anti-spoof gate a sample it cannot reason about, to publish a fix the channel would reject, was the wrong trade. Three tests `8b7d589ea` left failing are green again; their fixtures now carry capture times, as every real `expo-location` sample does. |
| `driver-app/lib/androidAuto/__tests__/carFixChannel.test.ts` | Assert acceptance explicitly before reading `.heading` | `adoptCarFix` returns `CarLatLng \| null` since `a6835daa9`; the tests still assumed non-null and left `tsc --noEmit` red on three errors. No production behaviour change. |

Trip recorder persistence, billing, ride state transitions, and backend writes are unchanged. Display-only filtering is downstream of raw trip recording. Heading/route changes will be scoped to Android Auto through opt-in marker props; common metadata plumbing also affects phone marker inputs. Check both marker forks listed in `docs/known-forks.md`.

## Before / after

Before: every callback overwrites position and derives heading in arrival order; camera uses current GPS while the icon uses delayed playback. A route segment can determine heading against observed travel.

After (as each checklist item lands): measurement-order acceptance; marker and camera share display state; route alignment respects movement direction and retained geometry covers playback.

## UX, alternative, and rollout

The driver should see the car aligned with travel on the head unit through all four compass headings and turns. No icon-art rotation is applied: rotating the bitmap by 180 degrees would also reverse currently correct southbound travel. Replacing the navigation SDK was considered; correcting the existing data/timing faults has a smaller blast radius and no added routing service cost.

No deployment, update publication or merge is authorized by this coding-only task. Device validation on the actual APK/update remains the release gate. A bundle switch for new rendering behavior is recorded with the final integration; it requires an update/restart and is not an instantaneous server kill switch.

## Verification and rollback

### Verification performed (2026-09-14, second session)

Run from the repo, using installed dependencies:

| Command | Result |
|---|---|
| `driver-app> npx tsc --noEmit` | **clean** (was 3 errors before this change — see the table above) |
| `rider-app> npx tsc --noEmit` | clean |
| `driver-app> npx jest lib/androidAuto __tests__/components/CarMarker.test.tsx` | **267 passed, 15 suites** (was 3 failing) |
| `rider-app> npx jest vehicleTracking markerPlayback carMarkerPositionChange CarMarkerParity RouteLine routeSegments` | 94 passed, 6 suites |
| `rider-app> npx jest driverArriving driverArrived homeScreen indexScreen rideOptions carMarker CarMarker vehicleTracking` | 337 passed, 8 suites |
| `driver-app> npx eslint` on the three changed driver files | 6 errors / 4 warnings — **identical set to a control lint of the committed versions**, so zero new findings. All pre-existing `react-hooks/*` issues in untouched regions of `CarMarker.tsx` plus an unused import in `carSurface.tsx`. |

Seven new `selectBearing` tests cover the reported scenario directly (south-directed route
vs northbound travel), the default-caller parity guarantee, route-agrees-with-course,
the travel-bearing veto, 359°/1° wrap-around, unusable references (`-1` / `null` / `NaN`),
and the at-rest case. Two new `carLocationTask` tests cover out-of-order batch selection
and the undated-batch regression.

One implementation bug was caught by its own test during development: the first version
applied the reference whenever movement was absent, which would have repainted the icon
every tick at a red light — reintroducing the placeholder-heading spin `hasMovementBearing`
exists to prevent. The reference is now applied only to *replace a movement-derived bearing
that was actually refused*.

### Adversarial review (spinr-edge-case-reviewer, against the actual diff)

Ran per CLAUDE.md pre-merge gate 10. It returned one blocker and three warnings; each was
re-verified by hand against the source before acting, not taken on trust.

- **Blocker, fixed.** `hasMovementBearingRef` was armed for `'route'`/`'travel'` but not for
  the new `'reference'` source, in both forks. Since `'reference'` is only returned *after*
  movement cleared `minMoveMeters`, it carries the same "movement established a direction"
  precondition — and in precisely the scenario this change targets (route *and* travel both
  contradicting) it can be the only source that ever fires, leaving the latch unarmed and
  reopening the raw-heading fallback. A car stopping after a vetoed stretch could then be
  spun to north by Android's placeholder `0`. Both gates now include `'reference'`.
- **Warning, acted on.** The `carLocationTask` fallback — reverted, see the table above.
- **Warning about turn behaviour** — superseded by the second review round below, which found
  the same problem from a different direction and raised `maxCourseErrorDeg` to 135°.
- **Warning, accepted and recorded.** `carSurface.tsx` reads `getHeadingSource()` during
  render while `here` comes from React state, so a render can pair an older fix with a newer
  source. Traced: `lastFix` and `lastHeadingSource` are always written together in the same
  synchronous call, and `normalizeHeading`'s null return plus `selectBearing`'s own
  `Number.isFinite` guard mean the worst case is "the veto does not fire for one frame",
  never "a wrong veto fires". That safety is a consequence of those guards rather than an
  enforced invariant — binding fix and source into one state slot would make it structural,
  and is a reasonable follow-up, not a blocker.
- Review independently **confirmed** the two load-bearing safety claims: default callers are
  byte-identical with `courseReference` omitted (every new predicate is constant-false and the
  new `coalescePlaybackBearing` guard is unreachable), and the reference provably cannot apply
  at rest.

### Second review round (`/code-review high`)

Three findings landed against this change and were fixed; two more restated things already
recorded; **four are pre-existing defects in the previously committed work and are NOT fixed
here** — see the follow-up list below.

- **The reference could reintroduce the exact bug (fixed).** Accepting `headingSource === 'gps'`
  was unsafe. Android returns a literal `0.0` from `getBearing()` when `hasBearing()` is false,
  and `resolveHeading` falls through to its `'gps'` branch whenever movement could not supply a
  course — *including* when the previous fix is older than the baseline window (backgrounding,
  tunnel, Doze), which is precisely when the car may be moving fast. A placeholder `0` adopted
  there would veto a correct southbound route and pin the icon north, then latch it. The
  reference is now restricted to `'derived'` — a bearing computed from two real positions,
  which no platform placeholder can reach. Cost: the veto is inactive when the course is not
  movement-derived; that degrades to today's behaviour, and the `courseRef` debug fact says
  which case is live.
- **Playback misalignment (fixed by re-tuning).** `courseReference` describes roughly *now*,
  while the marker deliberately renders ~5 s behind (`PLAYBACK_DELAY_MS`). Through a turn the
  two legitimately describe different pieces of road, so at a 90° threshold every sharp corner
  would veto a *correct* route bearing and snap the icon onto the post-turn course seconds
  early — trading a permanent fault for a frequent one. `maxCourseErrorDeg` is now **135°**:
  this veto targets geometry pointing *backwards* (~180°), not merely sideways, and 135
  separates those cleanly. Two tests lock the boundary (a 90° corner is not vetoed; 150/180/210
  still are). A U-turn completed inside the playback window can still misfire — rare and
  self-correcting, unlike what it replaces. Carrying the course in the playback buffer so the
  veto compares against a time-aligned reference is the principled fix, and belongs with plan
  Task 6/7 rather than here.
- **Headless throw (fixed).** The batch reducer dereferenced `fix.timestamp` unguarded; a null
  entry would throw inside a `TaskManager` handler, where there is no screen to show it on. Now
  `!fix?.coords || !Number.isFinite(fix.timestamp)`.
- Restated and already recorded: `trackingV2`/`fixAccuracyM` are inert (deliberate staged
  rollout), and neither app has visual-regression tooling.
- Also noted: `shared/utils/markerRouteTracking.ts` (from `74d50137f`) is **entirely dead
  code** — nothing imports it, it has no test, and it encodes a *60°* tolerance that would
  disagree with the 135° that shipped. Wire it with a reconciled constant or delete it; do not
  leave two gates with different thresholds.

### Pre-existing defects found in the committed work — NOT fixed here

These are in `a6835daa9` / `8b7d589ea`, not in this change, and each needs its own diff,
threshold decision and verification. Listed so they are not lost:

1. **(High) A 15 s course-baseline constant is reused as a hard position-drop threshold.**
   `carFixChannel.ts:269` rejects any fix where `now - capturedAt > MAX_COURSE_BASELINE_AGE_MS`
   (15 s). But `backgroundLocation.ts:413` starts the shared service with
   `deferredUpdatesInterval: interval`, and `IDLE_CADENCE.timeInterval` is **30 s** — verified.
   Android therefore routinely delivers batches older than the drop threshold, so an idle
   driver with the screen off can have every fix discarded and the marker freeze. Rejection
   also returns before `persistFix`, so the shared last-location cache goes stale and a later
   cold start may draw no marker at all. Staleness for *rendering* and staleness for *deriving
   a course* need two different numbers.
2. **(Medium) The starvation alarm cannot fire in that failure mode.** `arrivedSinceRead += 1`
   (`carFixChannel.ts:263`) increments *before* the rejection gate, so `carSession.ts`'s health
   check still sees a healthy fix rate while every fix is being discarded and the marker is
   frozen — a silent monitor, which that file's own two-counter comment argues against.
3. **(Medium) The staleness watchdog may never quiesce.** `lastFixAt` only advances on
   acceptance, so when Android returns the same cached fused location the monotonic arm rejects
   it, `carFixAgeMs()` stays above `STALE_AFTER_MS` (5 s), and the 3 s watchdog fires an
   `Accuracy.High` one-shot every tick for the whole session.
4. **(Low) `useCarLocation.ts:137` silently dropped a documented `force`** on the startup
   one-shot's `persistFix`, removing a deliberate throttle bypass with no stated rationale.

### What was NOT verified

- **The blocker fix has no regression test.** `hasMovementBearingRef` lives inside `CarMarker`,
  so covering "a `'reference'` tick arms the latch" needs a rendered marker driven through
  playback ticks rather than a pure-function test. The fix is a two-line predicate whose
  correctness is argued from `selectBearing`'s own preconditions, and it is not proven by a
  test. Worth adding alongside plan Task 6's real-geometry marker regressions.
- **No native build, no DHU, no head-unit run.** Jest + `tsc` prove the bearing algebra and
  the wiring compile and behave; they cannot prove what Google Maps renders on a head unit.
  Device validation remains the release gate.
- **The root cause is derived, not observed.** The "~180° pinned" conclusion is geometry
  applied to the driver's four verbal observations plus a confirmed course-up camera. No
  runtime capture of the marker's actual bearing exists. If the next drive still shows a
  reversed icon, the new `courseRef` debug fact is what distinguishes "no corroborated
  course, so route geometry went unchecked" from "course was available and the route agreed
  with it anyway" — two different bugs.
- **The debug panel is still off** (`carDebug.ts`'s `DEBUG_PANEL_ENABLED = false`), so that
  fact is recorded but not displayed. Flipping that constant is a one-line, separately
  reviewable change.
- **Photographs do not identify the installed revision.** The driver reports a build from
  the last day or two; that is not the same as a verified `runtimeVersion` / update ID.
- **No visual-regression tooling exists for driver-app or rider-app at all**, so the icon's
  rendered orientation was reasoned about, not screenshotted. (admin-dashboard's Playwright
  baselines are untouched by this change — no admin surface is involved.)
- `trackingV2` and `fixAccuracyM` remain unwired by every caller; the chronology guards
  inside `CarMarker` are therefore still inert by design.
- Plan Tasks 5, 7 and 8 (playback-route continuity, camera/marker single timeline, live-route
  ordering) are **not** implemented.

Rollback after a release requires republishing the preceding compatible bundle or disabling the rendering switch in a replacement update, followed by app restart/update adoption. No DB rollback applies. Source commits alone do not roll back an installed app.
