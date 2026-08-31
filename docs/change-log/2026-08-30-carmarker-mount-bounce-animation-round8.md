# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | shared, driver-app, rider-app (consumer of `shared/`) |
| Domain (Sentry tag) | drivers / rides |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`, commit `a00e89f`) |
| Related issue or gap ID | Round 8 — follow-up to round 7's [PR #4720](https://github.com/srikumarimuddana-lab/spinrvm/pull/4720), answering the open question: "can we take the liberty of make a new icon or animation, or is it dependent on Google/Waze maps" |

## 1. Issue / gap identified

Not a bug — a product question from round-7 live testing: whether a custom car-marker icon/animation is possible, or constrained by the Google Maps / Waze SDK.

## 2. Root cause

N/A (feature, not a fix). Confirmed by code inspection: `CarMarker.tsx` already renders our own transparent PNG as a `Marker` child (both `shared/components/CarMarker.tsx`, used by rider-app, and driver-app's own near-duplicate copy) — not a native Google Maps pin. So a bespoke icon or animation was already fully within our control; nothing needed to change to make that true, only to demonstrate it.

## 3. Fix / remediation

Added a one-shot spring scale+opacity "pop in" animation that plays once per `CarMarker` mount — including on the round-7 `mapKey` remount that recovers a stale marker after a driver goes offline then back online, so that fix now visibly pops the car back in rather than it silently reappearing.

Deliberately **not** a looping/pulsing animation. `rider-app/app/(tabs)/index.tsx` renders a `CarMarker` per nearby driver (`displayDrivers.map(...)`) — potentially many simultaneously. A continuous animation would force Android's `tracksViewChanges` to stay `true` forever per marker, re-snapshotting every one of them every frame — exactly the performance problem the component's existing settle-then-freeze snapshot lifecycle (extensively commented in the file, built up over rounds 1–6) exists to avoid. A short one-shot spring finishes within that existing post-image-load settle window, so it adds no extra Android re-snapshotting and costs nothing on the JS thread anywhere — `opacity`/`transform:scale` both support React Native's native driver, unlike the marker's `rotation` prop (a non-style native prop, JS-driven only).

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface but shared.** Both copies of `CarMarker.tsx` are used across driver-app's own map screen and 5 rider-app screens (`ride-in-progress.tsx`, `driver-arriving.tsx`, `ride-options.tsx`, `(tabs)/index.tsx`, `driver-arrived.tsx`, per grep). All consumers pass only the existing, unchanged prop surface — no prop signature changed, so no call site needed updating.
- **Perf**: the specific risk this change had to avoid — a continuous per-marker animation degrading the rider-app nearby-drivers map, which can render several `CarMarker`s at once — was designed out from the start (one-shot only, native-driven, timed to fit inside the existing settle window). No change to the playback/rotation/Android-native-animator logic that the file's extensive prior-round work built.
- **`View` import removed** from both files (now unused — the wrapper it was on became `Animated.View`); confirmed via grep no other `View` usage remained in either file before removing.
- No change to ride state, money, dispatch, or any backend path — this is a pure client-side visual component.

## 5. User-experience effect

- **Rider- and driver-facing**, visible mid-session (a driver going online, or a rider's map loading/refreshing nearby drivers) — by design, this is exactly the polish being added.
- Not feature-flagged: a small, additive, purely visual, single-shot animation with no functional change to marker position/rotation/tracking logic underneath it; consistent with how round 7's hud-collapse UI change (also visual-only, also un-flagged) was justified.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/CarMarker.tsx` | Added `mountAnim` (spring 0→1 on mount), wrapped the car-image `View` in `Animated.View` using it for opacity+scale, removed now-unused `View` import | New animation (rider-app + any future shared consumer) |
| `driver-app/components/CarMarker.tsx` | Same change, mirrored into driver-app's own near-duplicate copy | New animation (driver-app) |
| `driver-app/__tests__/components/CarMarker.test.tsx` | New file: 2 tests — mounts/ticks/unmounts cleanly, and remounting (simulating the mapKey-remount case) doesn't throw | Test coverage — first-ever test file for this component |

## 7. Before / after

```tsx
// Before
<View style={{ width: size, height: size, backgroundColor: 'transparent', alignItems: 'center', justifyContent: 'center' }}>
  <Image source={...} ... />
</View>
```
```tsx
// After
const mountAnimatedStyle = {
  width: size, height: size, backgroundColor: 'transparent',
  alignItems: 'center', justifyContent: 'center',
  opacity: mountAnim, transform: [{ scale: mountAnim }],
};
// ...
<Animated.View style={mountAnimatedStyle}>
  <Image source={...} ... />
</Animated.View>
```
with, added earlier in the component:
```tsx
const mountAnim = useRef(new Animated.Value(0)).current;
useEffect(() => {
  Animated.spring(mountAnim, { toValue: 1, friction: 6, tension: 80, useNativeDriver: true }).start();
}, []);
```

## 8. Rollback plan

Pure client-side, additive visual change — `git revert` of commit `a00e89f` is a complete rollback. No server state, no migration, no flag.

## 9. Verification performed

- [x] Automated tests: new `CarMarker.test.tsx` (2 tests, driver-app) passing. Full driver-app suite: **119 suites / 1334 tests, all passing**. Full rider-app suite: 133/134 suites, 1891/1896 tests — the 5 failures are the same pre-existing `rideDetailsScreen.test.tsx` failures tracked separately as [CR-2026-030 (#4722)](https://github.com/srikumarimuddana-lab/spinrvm/issues/4722); unrelated to this diff (that test doesn't touch `CarMarker`).
- [x] `npx tsc --noEmit` clean in both `driver-app/` and `rider-app/`.
- [x] `npx eslint` on both changed component files and the new test file: 0 errors (2 pre-existing-pattern `no-require-imports` warnings in the test file, matching established codebase style for `jest.mock()` factories).
- [x] Blast-radius grep: confirmed all `CarMarker` consumers (6 screens across both apps) pass only unchanged props; confirmed `View` import removal was safe (no other usage in either file).
- [ ] Manual repro on staging/device — not performed; this ships alongside/after round 7 for the user's next on-device test pass.
- [x] Reviewed against CLAUDE.md conventions: no state-machine/money/RLS/PIPEDA surface; Performance SLA table doesn't cover this path directly, but the perf-avoidance reasoning above directly addresses the anti-pattern class CLAUDE.md's SLA section calls out ("WebSocket broadcast to all connections instead of targeted fan-out" — same shape of mistake, avoided here for marker animation).
- [x] Feature-flagged if user-visible and non-trivial: not flagged — justified in §5, consistent with round 7's precedent for small un-flagged visual polish.

## 10. What was NOT verified

- No on-device/visual verification of how the bounce-in actually looks or feels ("playful" is subjective) — neither app has visual-regression tooling (standing gap, `ACTION_ITEMS.md`). The spring parameters (`friction: 6, tension: 80`) were chosen to be brisk and lively without excessive rebound based on read-through, not tuned against a device.
- No verification of the animation's timing relative to a real cold-start image decode on a low-end Android device — reasoned about (native driver, short duration, existing settle-window headroom) rather than measured. If a very slow image decode pushes past the spring's completion, the worst case is simply a missed visual (car appears already at full scale) — not a correctness or perf regression.
