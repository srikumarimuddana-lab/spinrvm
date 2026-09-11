# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Live-testing report: vehicle icon missing on Android, present on iOS and on a GrapheneOS Android device |

## 1. Issue / gap identified

On some Android devices, the vehicle marker icon in rider-app never appears — either nothing renders where the car should be, or (on screens with a status ring) only the colored ring shows. iOS is unaffected. The symptom is device/timing-dependent: it doesn't reproduce on every Android device, which is consistent with a race condition rather than a deterministic bug.

## 2. Root cause

`shared/components/CarMarker.tsx` (the component rider-app uses for every vehicle marker) is missing three Android-specific fixes that `driver-app/components/CarMarker.tsx` — a separate, drifted copy of the same original component — already has, each one previously shipped to fix this exact class of bug on the driver side:

1. **Mount animation raced the Android snapshot.** Android's map library renders custom markers as a one-time bitmap snapshot of the marker's view, not a live view. The car icon's "pop in" mount animation started at `opacity: 0, scale: 0` on every platform and faded in via a native-driven spring. If Android's snapshot fires while that fade-in is still near-invisible — a pure timing race, more likely on slower devices — the frozen bitmap is a blank (or ring-only) marker forever, since nothing later re-triggers a fresh capture.
2. **A completed image load didn't force a fresh snapshot.** `handleImageLoaded` called `setTracksViewChanges(true)` when the value was already `true`, which React treats as a no-op — no re-render, no new snapshot capture.
3. **A failed image decode had no retry path.** `onError` only ever flipped a custom-vs-bundled-image flag, which is a no-op when there's no custom image (the common case) — a transient decode glitch (OOM, codec hiccup on a low-end device) left the marker permanently blank with nothing to self-heal and nothing reported to monitoring.

## 3. Fix / remediation

Ported all three fixes from `driver-app/components/CarMarker.tsx` into `shared/components/CarMarker.tsx`:

1. The mount animation now starts at `opacity: 1, scale: 1` on Android (no fade-in to race the snapshot); iOS is unchanged (still starts at 0 and springs in — no snapshot race there, `Marker.Animated` re-renders live).
2. `handleImageLoaded` now forces a `tracksViewChanges` `false → true` transition (via `requestAnimationFrame`) instead of a same-value no-op set, so a completed image load always gets a fresh snapshot attempt.
3. A failed decode now retries up to 3 times with backoff (remounting the `Image` via a bumped `key`), and reports to Sentry (`captureException`, `domain: 'rides'`, `surface: 'rider-app'`) exactly once if all retries are exhausted — visible in monitoring instead of a silently broken icon.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (rider-app), isolated to one shared component.** Grepped the whole repo for every consumer of `shared/components/CarMarker`: only rider-app's `app/(tabs)/index.tsx` (nearby-drivers map), `ride-options.tsx`, `driver-arriving.tsx`, `driver-arrived.tsx`, and `ride-in-progress.tsx` import and render it. `driver-app` has its own separate copy (`driver-app/components/CarMarker.tsx`, already fixed) and does not import the shared one. `admin-dashboard`'s `vehicle-types/page.tsx` only *mentions* the shared component in a comment — it does not import it.
- No change to any prop, exported type, or call-site API — every fix is internal to the component's Android rendering path. Callers are unaffected.
- iOS rendering path is untouched (all three fixes are gated on `Platform.OS === 'android'` or don't apply to the iOS code path).
- No interaction with the ride state machine, background loops, or money/wallet paths — this is a pure map-rendering fix.

## 5. User-experience effect

- **Rider-facing.** Riders on affected Android devices/builds will now reliably see the vehicle icon (nearby drivers on the home screen, and the assigned driver on ride-options/driver-arriving/driver-arrived/ride-in-progress) instead of a blank marker or ring-only marker.
- Not visible mid-session in a way that changes behavior for anyone *not* hitting the bug — this only changes what happens during the brief marker-mount window, which already re-runs on every screen navigation and remount.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/CarMarker.tsx` | Added `MAX_IMAGE_RETRIES` constant; imported `captureException`; changed `mountAnim`'s initial value and effect to skip the fade-in on Android; changed `handleImageLoaded` to force a false→true `tracksViewChanges` transition; added `imageAttempt`/`imageRetryTimerRef`/`imageErrorReportedRef` state and a `handleImageError` retry-with-backoff-then-report callback; wired `key={imageAttempt}` and `onError={handleImageError}` onto the `ExpoImage` | Port the three Android icon-visibility fixes already proven in `driver-app/components/CarMarker.tsx` |
| `rider-app/__tests__/carMarkerPositionChange.test.tsx` | Added a `captureException` mock; added two new `describe` blocks ported from `driver-app/__tests__/components/CarMarker.test.tsx` — decode-failure retry/report behavior, and the Android-starts-visible mount behavior | Regression coverage for the three ported fixes |

## 7. Before / after

```tsx
// Before — mountAnim always starts invisible, even on Android
const mountAnim = useRef(new Animated.Value(0)).current;
useEffect(() => {
    Animated.spring(mountAnim, { toValue: 1, friction: 6, tension: 80, useNativeDriver: true }).start();
}, []);
```

```tsx
// After — Android starts visible; only iOS plays the fade-in
const mountAnim = useRef(new Animated.Value(isAndroid ? 1 : 0)).current;
useEffect(() => {
    if (isAndroid) return;
    Animated.spring(mountAnim, { toValue: 1, friction: 6, tension: 80, useNativeDriver: true }).start();
}, []);
```

```tsx
// Before — a no-op when tracksViewChanges is already true
const handleImageLoaded = () => {
    hasLoadedImageRef.current = true;
    setTracksViewChanges(true);
    settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
};
```

```tsx
// After — forces a real false→true transition
const handleImageLoaded = () => {
    hasLoadedImageRef.current = true;
    setTracksViewChanges(false);
    requestAnimationFrame(() => {
        setTracksViewChanges(true);
        settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
    });
};
```

## 8. Rollback plan

`git revert` is sufficient and complete here — this is a pure client-rendering change with no data written anywhere (no DB writes, no Stripe/wallet state, no ride-state changes). Reverting the commit restores the previous (buggy but not data-affecting) behavior. No feature flag was added: this is a bug fix to existing, already-shipped rendering code, not a new user-visible feature, and the change is behaviorally a no-op except in the exact race condition it fixes.

## 9. Verification performed

- [x] Automated tests run: `rider-app/__tests__/carMarkerPositionChange.test.tsx` (11/11 passing, including 2 new describe blocks — 3 tests for decode-retry, 2 tests for the Android-starts-visible mount fix), plus the full rider-app suite for every screen that renders this component: `homeScreen`, `rideInProgressScreen`, `driverArrivingScreen`, `driverArrivedScreen`, `rideOptionsScreen`, `markerPlayback`, `vehicleTracking`, `gpsSmoothing` (339/339 passing across 7 suites).
- [x] `npx tsc --noEmit` run clean (real TypeScript compile, not just a dev server) — no errors.
- [x] `npx eslint` run on both changed files — 0 errors (2 pre-existing warnings in the test file's own `jest.mock('react-native-maps', ...)` boilerplate, unrelated to this change).
- [x] Blast-radius grep performed: `grep -rl "shared/components/CarMarker"` across the whole repo — every real consumer listed in §4.
- [x] Reviewed against CLAUDE.md conventions: no state-machine, money, RLS, or PIPEDA surface touched; Sentry tagging on the new `captureException` call follows the `domain`/`surface` convention.
- [ ] Feature-flagged: not applicable — see §8 for why (bug fix to existing rendering code, not a new user-visible feature).

**What was NOT verified:** no real Android device was available in this environment to visually confirm the icon now appears — rider-app has no automated visual-regression tooling (per CLAUDE.md, this is a known, accepted gap for rider-app/driver-app). This fix is reasoned about and unit-tested (the exact internal state — `tracksViewChanges`, the mount `Animated.Value`'s starting value, the retry/report behavior — is asserted directly), not screenshotted. It mirrors, line-for-line, a fix already verified working in production on driver-app's identical component, which is the strongest available evidence short of an on-device rider-app test.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: the icon now reliably appears where it sometimes didn't)
