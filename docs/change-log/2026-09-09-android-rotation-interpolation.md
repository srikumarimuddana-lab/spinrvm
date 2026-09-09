# Change Impact & Risk Log — Android car icon rotation snapped through turns instead of tweening

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/vehicle-icon-movement-animation-8tys8o` |
| Related issue or gap ID | User-reported, live-tested Android build (2026-09-09): "latency jitter and no smooth animation" for the driver's own vehicle marker |

## 1. Issue / gap identified

On Android, the driver's own vehicle marker's rotation (which way the car icon points) updated as a single, un-tweened step every 500ms (`TICK_MS`), while its position animates smoothly via the native `animateMarkerToCoordinate`. Position looked smooth; heading did not — most noticeably through turns, where the angular change within one 500ms window can be large.

## 2. Root cause

A dedicated investigation agent traced this precisely: `react-native-maps`' plain `Marker` (used on Android instead of `Marker.Animated`, for reasons already documented in `CarMarker.tsx` — `animateMarkerToCoordinate` cannot be called on `Marker.Animated`, per `react-native-maps#3913`) does not accept an `Animated.Value` for its `rotation` prop the way it does for `Marker.Animated`. iOS's marker uses `Marker.Animated` + a real `Animated.Value` (`rotationAnim`), tweened via `Animated.timing` in `animateRotationTo`, so a bearing change sweeps smoothly across the tick duration. Android instead set `androidRotation` (plain React state) directly to the new bearing once per tick — a discrete jump, not a sweep. The existing code comment reasoned this was "visually smooth at 2 steps/second" because the spline bearing is C¹-continuous between consecutive *targets* — true, but that reasoning only bounds how far apart two targets are, not how large an un-tweened step between them can still be during a turn (30–90°+ within one 500ms window at an intersection), which is exactly what a driver would perceive as the reported "no smooth animation."

## 3. Fix / remediation

Added a `requestAnimationFrame`-driven interpolation loop for Android rotation only (`stepAndroidRotation` / `animateAndroidRotationTo` in `CarMarker.tsx`), mirroring what `Animated.timing` already does for iOS but implemented by hand since the plain `Marker`'s `rotation` prop only accepts a number, not an `Animated.Value`:
- Each new bearing still resolves its shortest-arc *target* via the existing `shortestArcRotationTarget` helper (already used by iOS), so rotation never spins the long way around.
- The tween's "from" value is read from wherever the rotation actually currently is (`androidRotationCurrentRef`, updated every frame), not the previous tick's target — so a new bearing arriving mid-tween doesn't cause a visible jump to the old target before starting the new leg.
- Duration matches `TICK_MS` (capped at `MAX_ROTATE_MS`, same as iOS), so rotation and position finish their respective animations together.
- The RAF loop only runs while a tween is in progress; it stops itself once `t >= 1` and is cancelled on unmount — no continuous polling while the vehicle is parked (the ticker's existing "movedM < 0.5 → no-op" guard already prevents new tween targets from being set while stationary).

Position animation (`animateMarkerToCoordinate`), the ring/snapshot-freeze logic, and iOS's rotation path are all untouched.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `CarMarker.tsx`'s Android-only rotation path.** No new props, no change to the component's public interface. iOS is completely unaffected — it never enters any of the new code.
- Grepped both known callers (`driver-app/app/driver/(tabs)/index.tsx`, `driver-app/lib/androidAuto/carSurface.tsx`) — neither reads `androidRotation` or any rotation-internal state directly; both only consume `onBearingChange`, which still fires with the exact same selected bearing value as before (the interpolation is purely visual, the *selected* bearing and the `onBearingChange` callback's value are unchanged).
- Does not touch the ring-freeze/`tracksViewChanges` logic (fixed twice already this session) — rotation prop updates on a plain `Marker` are handled by the native map SDK directly, independent of the JS-child-view snapshot that `tracksViewChanges` gates, so more frequent rotation updates cannot affect snapshot freezing.
- Adds one `requestAnimationFrame` loop per marker instance, active only while actively rotating (not idle/parked) — negligible cost on the single-marker screens this component's `ring` prop doc comment already scopes it to.

## 5. User-experience effect

- **Driver-facing (Android only)**: the car icon should now visibly sweep through a turn instead of snapping to a new heading twice a second. No change to how fast the marker reaches its final orientation — only how it gets there.
- iOS: no change (already tweened before this fix).
- Not a change to any state-machine or navigation-instruction behavior — purely the icon's own visual rotation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | Added `stepAndroidRotation`/`animateAndroidRotationTo` (a hand-rolled RAF tween for Android's non-animatable `rotation` prop); ticker's Android branch now calls `animateAndroidRotationTo` instead of setting `androidRotation` directly | Close the un-tweened-rotation gap found by this session's investigation |
| `driver-app/__tests__/components/CarMarker.test.tsx` | New `describe` block: rotation passes through intermediate values during a turn (not a single jump) and settles exactly at the target; RAF loop doesn't throw across repeated ticks or on unmount | New behavior needs test coverage |

## 7. Before / after

```tsx
// Before
if (isAndroid) {
    setAndroidRotation(((bearing % 360) + 360) % 360);
} else {
    animateRotationTo(bearing, TICK_MS);
}
```

```tsx
// After
if (isAndroid) {
    animateAndroidRotationTo(bearing, TICK_MS);   // RAF-interpolated shortest-arc tween
} else {
    animateRotationTo(bearing, TICK_MS);
}
```

## 8. Rollback plan

No migration, no live data. Pure component-logic diff in one file — `git revert` is a complete rollback, restoring the prior single-step behavior exactly. No feature flag: this is a visual-smoothness fix to an existing rendering path with no corresponding `app_settings` flag to piggyback on, and the change is purely additive (a new interpolation layer) rather than replacing a correctness-critical mechanism.

## 9. Verification performed

- [x] `yarn jest __tests__/components/CarMarker.test.tsx` — 20/20 passed, including 2 new tests proving interpolation (intermediate rotation values sampled mid-tween) and settlement at the exact target
- [x] `yarn jest` (full driver-app suite) — 132 suites / 1497 tests passed
- [x] `npx tsc --noEmit` — clean
- [x] Blast-radius grep: both known `CarMarker` call sites checked, neither reads Android-rotation-internal state
- [ ] **`npm run build` / EAS production build NOT run** — OTA-eligible JS-only change, no native deps touched
- [ ] Not manually verified on a real Android device — the fix was verified via the component's own animation-frame timing under test (fake timers + RAF), not a visual comparison on hardware, since driver-app has no visual-regression tooling and this environment has no device/emulator

## What was NOT verified

- **Whether this specific mechanism (stepped vs. tweened rotation) is actually what the user perceived as "jitter"/"no smooth animation," as opposed to a different cause** — this was the highest-confidence hypothesis from this session's dedicated investigation (file:line evidence: the code's own "visually smooth at 2 steps/second" comment doesn't account for angular rate during turns), but it is reasoning from code, not a confirmed device reproduction. If the user's report was actually about *position* stutter rather than *rotation* snap, this fix does not address that and needs separate investigation (the same investigation ruled out `PLAYBACK_DELAY_MS` as a jitter cause — it's a fixed lag, not stutter — and flagged upstream GPS-fix cadence as a lower-confidence secondary factor, not yet acted on).
- No visual-regression tooling exists for driver-app — reasoned about and unit-tested at the animation-timing level, not screenshotted before/after.
- Turn-by-turn navigation latency (the user's separate complaint) is **not** addressed by this fix — that investigation found the driver-app phone screen has no in-app turn-by-turn instruction UI at all (it deep-links to Waze/Google/Apple Maps for that); the actual latency is in the live-route polyline/ETA refresh, polled every 20 seconds. That is a separate, not-yet-actioned finding — see the session's ongoing discussion with the user before deciding whether/how to change that poll interval.
