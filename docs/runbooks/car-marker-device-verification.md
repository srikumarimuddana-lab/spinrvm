# Car Marker Device Verification Checklist

**Purpose:** the human-execution device pass ACTION_ITEMS.md C90 and C103 have
been tracking as open since 2026-09-07/09-12. Five behavior-changing fixes to
`shared/components/CarMarker.tsx` (rider-app's on-map driver icon) have only
ever been verified by unit/component tests and code review — nobody has
watched any of them actually render on a phone. rider-app has **zero**
automated visual-regression tooling (per `CLAUDE.md` §6), so this checklist is
the only thing that catches a visually-wrong-but-non-crashing regression here.

**When to run:** now, to close out C90/C103's standing device-access gap.
After that, per C103's own cadence rule: "after any change to
`shared/components/CarMarker.tsx`" — not calendar-fixed.

**Scope — what this covers:** the rider-app phone screen only (5 screens:
`ride-options.tsx`, `ride-in-progress.tsx`, `driver-arrived.tsx`,
`driver-arriving.tsx`, `(tabs)/index.tsx`). Five distinct behaviors, all in
`shared/components/CarMarker.tsx`:

| # | Behavior | Platform | Source |
|---|---|---|---|
| 1 | Route-segment continuity hint (`preferredFromIndex`) | Both | C90 fix #1 |
| 2 | GPS pre-smoothing + implausible-jump rejection | Both | C90 fix #2 |
| 3 | Ring-change re-arm of the frozen marker snapshot | Android only | C90 fix #3 |
| 4 | Route re-anchoring on a live-route re-poll ("car drives sideways") | Both | REC-C-04 |
| 5 | Android rotation cadence (intentionally step-per-tick, see §5's note) | Android only | REC-C-03 (superseded, see below) |

**Explicitly out of scope — do not try to fold these into this pass:**
- **C70** (Android Auto head-unit/DHU projection) — a distinct concern per
  C90's own filing; needs an EAS dev build + Android Auto Desktop Head Unit,
  not covered here.
- **C97 #5** (iOS `UIBackgroundModes` push-in-background confirmation) and
  **REC-A-10** (iOS Live Activity lock-screen rendering) — different surfaces,
  different builds required (TestFlight/Xcode).
- driver-app's own copy of this component — already proven live in production
  since 2026-09-05/09/11/14 (see the dated fixes each behavior below cites).
  You do not need to re-verify driver-app; it's useful only as a known-good
  reference to compare rider-app against (see §6).

---

## 1. Prerequisites

- [ ] A physical Android phone. The ring-freeze bug (#3) is an Android
      `tracksViewChanges`-snapshot-specific defect — **iOS cannot exercise
      it**, by design (`Marker.Animated`'s path has no equivalent freeze).
      Behaviors #1, #2, and #4 apply to both platforms; if you have an iPhone
      too, run those three on both, but Android is the one that must happen.
      An Android emulator is an acceptable substitute for #1/#2/#4, but is
      known to render `react-native-maps`/Fabric markers differently enough
      that it should **not** be treated as sufficient for #3 or #5 — those
      two need real hardware.
- [ ] Latest build of rider-app from branch `main` (or whichever branch you're
      verifying), installed on the device — not just `expo export --platform
      web`, which is what every prior attempt at this had to settle for.
- [ ] A driver-app device (or emulator) running the same backend/environment,
      so you can create a live ride and move the driver's location — you need
      to *be* the driver moving, or have someone else drive/simulate movement,
      while you watch the rider screen.
- [ ] A test route that includes **at least one intersection or divided
      road** (two parallel carriageways close together) — behaviors #1 and #4
      specifically need this geometry to have anything to detect. A single
      straight road will not exercise either fix.
- [ ] Screen recording running on the rider device for the whole pass (60fps
      if the device supports it) — some of these defects are a single-tick
      (500ms) visual glitch that's easy to miss live and easy to confirm on
      playback.

---

## 2. Behavior #1 — Route-segment continuity hint (`preferredFromIndex`)

**What it fixes:** without this hint, `snapToRoute`'s nearest-segment search
is purely spatial — on a divided road or near an intersection, the marker's
route-snap can pick a nearby segment that points the *wrong direction*,
causing the car icon's bearing to flip ~180° for one tick before correcting.

**How to trigger:** drive (or simulate driving) the assigned driver through
an intersection or along a divided road, at normal city speed, while watching
the rider screen on `ride-options`, `driver-arriving`, or `ride-in-progress`.

**Pass:** the car icon's heading changes smoothly through the turn/segment
transition. No single-tick flip to a bearing that doesn't match the visible
direction of travel.

**Fail:** for one ~500ms tick (`TICK_MS`), the icon's heading snaps to a
direction roughly opposite or perpendicular to the actual travel direction,
then corrects itself on the next tick. This looks like a brief "twitch" or
"glance the wrong way."

---

## 3. Behavior #2 — GPS pre-smoothing + implausible-jump rejection

**What it fixes:** two related things — (a) single-fix GPS jitter (the car
icon vibrating/jumping slightly even when the vehicle is moving smoothly or
stationary), and (b) a physically-impossible speed jump (GPS multipath/noise,
not a real position) should be dropped rather than rendered as a teleport.

**How to trigger (a):** watch the marker while the driver is stationary
(stopped at a light, parked) or moving at a slow, steady speed. GPS noise is
most visible when the vehicle isn't moving — any single fix has some jitter.

**How to trigger (b):** harder to trigger organically (needs a genuine GPS
glitch). If you can, toggle location permission off/on briefly, or drive
through an area with poor GPS reception (underpass, dense urban canyon,
parking garage) and watch what happens when signal returns.

**Pass (a):** the marker holds a visually stable position while stationary —
no visible micro-jitter/vibration. While moving, the path looks like a smooth
glide, not a jagged line.

**Fail (a):** the marker visibly jumps/vibrates by a small amount (a few
meters) tick-to-tick while the vehicle isn't actually moving that much.

**Pass (b):** if a GPS glitch occurs, the marker holds its last known
position rather than instantly teleporting, then resumes smoothly once real
fixes return.

**Fail (b):** the marker instantly snaps to an impossible location (e.g.
across the map) for one tick.

---

## 4. Behavior #3 — Ring-change re-arm (Android only)

**What it fixes:** on Android, `react-native-maps`' `Marker` freezes a
bitmap snapshot of its children once `tracksViewChanges` flips to `false`.
If a screen mounts with the colored status ring already present (a fresh
navigation between ride-stage screens, or reopening the app mid-ride) and the
snapshot freezes before the car icon image has finished decoding, the marker
can get stuck showing **only the colored ring — no car icon at all** — for
the rest of that screen's lifetime.

**How to trigger:** during an active ride, navigate away from and back to
a screen that renders the marker with a `ring` prop — specifically
`driver-arriving.tsx`, `driver-arrived.tsx`, or `ride-in-progress.tsx` (the
other two screens in scope, `ride-options.tsx` and the home tab, don't pass
`ring` and can't exercise this). Backgrounding and foregrounding the app
mid-ride, or force-navigating between these three screens rapidly, are the
highest-probability triggers. Also try: reopening the app fresh (kill and
relaunch) while a ride is already in `driver_arriving`/`driver_arrived`/
`in_progress` state, so the screen mounts with `ring` non-null on the very
first render.

**Pass:** the car icon is always visible whenever a status ring is shown —
you should never see a colored circle with nothing inside it, on any of the
three screens above.

**Fail:** a colored ring renders with no car icon inside it, and stays that
way — this is the more severe of the five defects (a fully missing icon, not
just smoothness), so treat any reproduction of this one as high priority even
if the other four all pass.

---

## 5. Behavior #4 — Route re-anchoring on live-route re-poll ("car drives sideways")

**What it fixes:** the in-trip live route re-polls roughly every ~20s, which
replaces the `routeCoordinates` array with a **new array reference** covering
the same physical road but re-indexed from the car's current position (what
was index 5 on the old array might be index 0 on the new one). Without
rebasing the continuity-hint index (behavior #1) against the new array, the
next `snapToRoute` search can restrict itself to segments *ahead* of the car
on the new indexing — and if the first segment in that window happens to be
past an upcoming turn, the icon shows a ~90°-off bearing while the car is
still driving straight. This was live-tested and named directly on driver-app
as **"the car drives sideways"** (2026-09-11).

**How to trigger:** this needs a re-poll to land *near* a turn, so timing
matters. Drive toward a turn/intersection and try to time it so you're
approaching the turn around the ~20s mark of the live-route refresh cycle —
or just drive a longer route with several turns and watch continuously
through each one; a 20s-cadence re-poll will eventually coincide with one.

**Pass:** through every turn, the marker's bearing tracks the vehicle's
actual direction of travel. No moment where the icon visibly points off to
the side relative to the road the car is actually on.

**Fail:** at some point (most likely right after a route refresh, near a
turn) the icon's heading is clearly wrong relative to the road — pointing
sideways or backward while the vehicle continues straight or through the
turn normally. This is the highest-priority behavior in this checklist per
the audit that found it (rated P1/RED, "the single highest-priority finding"
in the module report that surfaced it) — reproducing this on rider-app would
be a real, currently-unverified regression risk.

---

## 6. Behavior #5 — Android rotation cadence (context, not a strict pass/fail)

**Do not expect smooth per-frame rotation.** The code's own history here is
more nuanced than the original audit recommendation (REC-C-03, "port
driver-app's smooth interpolation"): a `requestAnimationFrame` tween was
added 2026-09-09, but after a 2026-09-14 Fabric `markerRotation` patch, those
per-frame prop diffs started re-pinning the marker's position at a stale
coordinate — a worse bug than the step-function rotation it was fixing. The
current code deliberately reverted to a once-per-tick (`TICK_MS` = 500ms)
rotation step as the "last-known-good cadence," per the comment directly
above `androidRotation`'s `useState` in `shared/components/CarMarker.tsx`.

**What to actually check:** not smoothness — **correctness of the Fabric
revert**. Specifically:
- [ ] The marker's **position** never appears to snap back to a stale
      coordinate mid-turn (this was the regression the revert fixed — if you
      see the car icon briefly jump backward along the route, that's the bug
      this revert exists to prevent, and it would mean the revert itself has
      a gap).
- [ ] The rotation step (once per ~500ms) is visually acceptable — a
      slightly less smooth turn animation than iOS is an accepted tradeoff
      here, not a defect to file. Only flag this if the rotation looks
      **broken** (e.g. spinning wildly, stuck, or wrong direction), not
      merely "less smooth than I'd like."

If you want to compare against what smooth interpolation would have looked
like, driver-app's copy still runs the `stepAndroidRotation`/
`animateAndroidRotationTo` implementation — but per the note above, that is
**not** the current target state for rider-app; do not treat a difference
from driver-app's rotation smoothness as a bug on its own.

---

## 7. Cross-check against driver-app (optional, but recommended)

driver-app's own copy of this component has had behaviors #1, #2, and #3
live in production since 2026-09-05, and behavior #4 since 2026-09-11 —
all without a reported regression in this codebase's change-log. If you have
a second device, running the same test route with driver-app's marker
(watching your own position as a driver) alongside rider-app's marker
(watching the assigned driver) is a fast way to build confidence that any
defect you see on rider-app is a genuine gap, not an artifact of your test
route/timing/network conditions. If driver-app shows the same glitch,
that's a *new*, previously-unreported regression on both apps — escalate
differently (see §9).

---

## 8. Sign-off

- [ ] Behavior #1 (continuity hint): PASS / FAIL — describe if FAIL
- [ ] Behavior #2 (GPS smoothing): PASS / FAIL — describe if FAIL
- [ ] Behavior #3 (ring re-arm, Android): PASS / FAIL — describe if FAIL
- [ ] Behavior #4 (route re-anchor): PASS / FAIL — describe if FAIL
- [ ] Behavior #5 (Android rotation, context check only): no regression
      observed / regression observed — describe if so
- [ ] Screen recording saved and attached to the result write-up

Device: `__________`  OS version: `__________`  App build/commit: `__________`
Tester: `__________`   Date: `__________`

---

## 9. After the pass — required follow-up (per C103's own instructions)

1. **Record the result as a dated `docs/change-log/` entry**, same pattern as
   `docs/change-log/2026-08-16-android-auto-hardware-validation.md` — even
   (especially) if everything passed. "No device pass exists" has been the
   standing gap; a clean result still needs a record, not just a verbal "looks
   fine."
2. **Update `ACTION_ITEMS.md` C90's own checklist** with the outcome — this
   is the item this whole pass exists to close.
3. **Update `ACTION_ITEMS.md` C103** — cross off item #2 (C90) in its
   consolidated list, and note whether this pass also covered the folded-in
   REC-C-03/REC-C-04 verification debt (§5 and §4 above) so that note doesn't
   need a second device session.
4. **If any behavior FAILs:** file it as a new, distinct ACTION_ITEMS entry
   (not folded into C90, which is specifically about "unverified," not "known
   broken") — a live-testing-confirmed regression is a different severity
   than "never watched," and should be triaged/prioritized separately, most
   likely P0/P1 given these are all rider-facing, currently-shipped defects
   if reproduced.
