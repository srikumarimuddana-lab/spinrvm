# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Follow-up flagged in `docs/change-log/2026-09-11-driver-marker-first-fix-snap.md` §Tier 2 "Not changing but considered" — the identical structural gap in `shared/components/CarMarker.tsx`, deliberately not touched in that PR (surgical scope) |

## 1. Issue / gap identified

`shared/components/CarMarker.tsx` (used by rider-app's ride-flow screens to render the assigned driver's vehicle) has the same defect just fixed in driver-app's own copy: after a remount, the first real GPS fix animates a fast straight-line glide across the real distance the driver has moved since the marker was last rendered, instead of snapping instantly.

## 2. Root cause

Identical to the driver-app fix: `ingestFix()`'s instant-snap path only ever fires via `shouldResetBuffer()` (`shared/utils/markerPlayback.ts`), which compares a new fix against the buffer's **last** entry and explicitly returns `false` when there is none. A brand-new/empty buffer's first-ever fix can therefore never trigger a snap.

rider-app remounts this component on every ride-phase screen transition (`ride-options.tsx` → `driver-arriving.tsx` → `driver-arrived.tsx` → `ride-in-progress.tsx`, and the nearby-drivers map in `(tabs)/index.tsx`) the same way driver-app's `mapKey` remount-on-going-online does — each screen mounts a fresh `CarMarker` instance with an empty buffer, so the first fix received on that screen is exposed to the same race.

## 3. Fix / remediation

Ported the driver-app fix verbatim: `ingestFix` now also treats an **empty buffer** as a reset trigger, alongside the existing distance check. Android unchanged (already had `setAndroidCoord`); iOS previously had no instant-seed at all — now calls `AnimatedRegion.setValue()` (guarded with a `typeof` check, matching the existing Android native-method-missing fallback style). `prevTargetRef` now resets on both platforms on this path (previously Android-only).

## 4. Risk & impact on existing functionality

- **Blast radius: single-file (`shared/components/CarMarker.tsx`), single-surface (rider-app).** Grepped the whole repo for every importer of `@shared/components/CarMarker` / `shared/components/CarMarker`: exactly 5 real consumers, all rider-app screens — `app/(tabs)/index.tsx`, `app/ride-options.tsx`, `app/driver-arriving.tsx`, `app/driver-arrived.tsx`, `app/ride-in-progress.tsx` — plus their own test files and the component's dedicated unit test. driver-app has its own separate copy (`driver-app/components/CarMarker.tsx`, already fixed in the prior PR) and does not import this shared one; admin-dashboard only mentions the shared component in a comment, no import. Unchanged from the blast radius documented for this same file in the earlier `docs/change-log/2026-09-10-rider-app-android-car-icon-missing.md` entry.
- No change to props, exported types, or call-site API.
- Interacts only with this component's own playback ticker (`prevTargetRef`/`animatedRegion`/`androidCoord`) — no interaction with the ride state machine, WS, or money/wallet paths.
- A first fix into an empty buffer now always snaps rather than gliding — this covers every remount trigger for this shared component (screen navigation, cold mount, ride-phase transitions), not narrowly scoped to one screen.

## 5. User-experience effect

- **Rider-facing only.** On any of the 5 affected screens, if the assigned driver's icon was stale when the screen mounted (screen navigation, backgrounding/foregrounding, app restart mid-ride) and a real position update arrives, the icon now jumps instantly to the driver's actual position instead of visibly sliding across the map through roads/buildings.
- Not visible mid-session in any way that changes behavior outside this exact remount-then-first-fix window — the ticker's ordinary tick-by-tick glide during continuous tracking is completely unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/CarMarker.tsx` | `ingestFix` now resets on an empty buffer too, not just a distance-triggered one; added an iOS `animatedRegion.setValue()` instant-seed (guarded); `prevTargetRef` now reset on both platforms on this path | Port the driver-app fix for the identical structural gap in this shared component |
| `rider-app/__tests__/carMarkerPositionChange.test.tsx` | Added `AnimatedRegion.setValue()` to the `react-native-maps` mock (matches the real library); added a new describe block asserting the instant-seed behavior on both platforms | Regression coverage for this exact fix, ported from driver-app's equivalent test |

## 7. Before / after

```tsx
// Before — only an EXISTING last fix could trigger the reset/snap path
if (shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
    bufferRef.current.length = 0;
    smoothingStateRef.current = null;
    hasMovementBearingRef.current = false;
    if (Platform.OS === 'android') {
        setAndroidCoord(rawCoord);
        prevTargetRef.current = rawCoord;
    }
    // iOS: no instant-seed at all — always glided.
}
```

```tsx
// After — an empty buffer (first fix after any remount) also resets/snaps
const isFirstFix = bufferRef.current.length === 0;
if (isFirstFix || shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
    bufferRef.current.length = 0;
    smoothingStateRef.current = null;
    hasMovementBearingRef.current = false;
    prevTargetRef.current = rawCoord; // now both platforms
    if (Platform.OS === 'android') {
        setAndroidCoord(rawCoord);
    } else if (typeof (animatedRegion as any).setValue === 'function') {
        animatedRegion.setValue({
            latitude: rawCoord.latitude,
            longitude: rawCoord.longitude,
            latitudeDelta: 0,
            longitudeDelta: 0,
        });
    }
}
```

## 8. Rollback plan

`git revert` is sufficient and complete — this is a pure client-rendering change with no data written anywhere (no DB writes, no Stripe/wallet state, no ride-state changes, no migration). Reverting restores the previous (buggy but not data-affecting) glide behavior. No feature flag: this is a bug fix to existing, already-shipped rendering code on a code path (`ingestFix`) with no external toggle, not a new user-visible feature — identical rationale to the driver-app fix this ports.

## 9. Verification performed

- [x] Automated tests run: `rider-app/__tests__/carMarkerPositionChange.test.tsx` (13/13 passing, including 2 new tests asserting the instant-seed on both Android and iOS), plus every real consumer screen's own test suite — `homeScreen.test.tsx`, `rideInProgressScreen.test.tsx`, `driverArrivingScreen.test.tsx`, `driverArrivedScreen.test.tsx`, `rideOptionsScreen.test.tsx` (295/295 passing across all 5).
- [x] `npx tsc --noEmit` run on rider-app — no errors attributable to this change.
- [x] Blast-radius grep performed: confirmed exactly 5 real screen consumers of `@shared/components/CarMarker`, all rider-app; confirmed driver-app's own copy is a separate file, not this one.
- [x] Reviewed against CLAUDE.md conventions: no state-machine, money, RLS, or PIPEDA surface touched.
- [ ] Feature-flagged: not applicable — see §8.
- [ ] Manual repro on a real device: **not performed** — see below.

**What was NOT verified:** no real device was available in this environment to reproduce the exact stale-marker-then-real-fix scenario on any of the 5 affected screens and visually confirm the glide is gone. rider-app has no automated visual-regression tooling (per CLAUDE.md, a known accepted gap for rider-app/driver-app). This fix is reasoned from the actual code paths involved (identical to the driver-app fix, which was itself verified against the real `react-native-maps` source for `AnimatedRegion.setValue()`) and unit-tested at the exact internal seam (the marker's rendered position immediately after the fix, before any ticker animation runs) — not screenshotted on a device.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4 — exactly 5 rider-app screens)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: instant snap instead of a cross-map glide, only on remount)
