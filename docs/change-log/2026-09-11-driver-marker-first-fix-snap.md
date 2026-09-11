# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Live-testing report 2026-09-11: driver's own vehicle icon "moved through the building using the shortest path" immediately after coming back online following an idle period spent offline, combined with a ~20s wait before the icon updated at all |

## 1. Issue / gap identified

After a driver went offline, moved a real distance, then went back online, the vehicle marker sat frozen at the old (stale) position for a noticeable delay, then animated a single fast straight-line glide directly to the new position — cutting across roads and buildings rather than snapping instantly, which the existing `SNAP_DISTANCE_M` "instant snap for a big jump" logic was specifically built to prevent.

## 2. Root cause

`CarMarker.tsx`'s `ingestFix()` only triggered the instant-snap path via `shouldResetBuffer()` (`shared/utils/markerPlayback.ts`), which compares a new fix's distance against the buffer's **last** entry — and explicitly returns `false` when there is no last entry (`if (!last) return false`). A brand-new/empty buffer's first-ever fix therefore could never trigger a snap, regardless of how far the real position had moved.

This is exactly the situation `index.tsx`'s `mapKey` remount-on-going-online produces (that remount exists for an unrelated prior fix — the marker never reappearing after offline→online, see its own comment): the remount tears down and recreates `CarMarker` fresh, seeded with the stale pre-offline coordinate and an empty buffer. The first real GPS fix after reacquiring a signal then lands as an ordinary ~500ms tween target instead of a reset anchor, animating one fast glide across whatever geography lies between the two points.

The separately-reported ~20s delay before any of this happens is GPS time-to-first-fix after being paused (a device/OS-level constraint, not something this fix addresses) — the two symptoms are sequential parts of the same event: freeze, then one fast uncontrolled glide, not two independent bugs.

## 3. Fix / remediation

`ingestFix` now also treats an **empty buffer** (`bufferRef.current.length === 0`) as a reset trigger, alongside the existing distance check. On that reset path:
- Android: unchanged — already called `setAndroidCoord(rawCoord)`.
- iOS: previously had **no** equivalent instant-seed at all. Now calls `AnimatedRegion.setValue()` (confirmed present in the installed `react-native-maps@1.27.2`, guarded with a `typeof` check matching the existing Android native-method-missing fallback pattern) to set the marker's position immediately, with no animation, so the next ticker tick's `.timing()` call starts from the real fix instead of gliding in from the stale mount position.
- `prevTargetRef` (the "from" position the ticker measures movement against) is now reset on **both** platforms on this path — previously only Android reset it, so iOS could still glide from a stale reference even on an ordinary large distance-triggered reset, not just the first-fix case.

## 4. Risk & impact on existing functionality

- **Blast radius: single-file, single-surface.** Only `driver-app/components/CarMarker.tsx` changed (plus its test file). Grepped the whole repo: `rider-app`'s own separate copy of `CarMarker.tsx` is a different file with the identical structural gap, **not touched by this change** — flagged as a follow-up candidate, not fixed here (surgical scope).
- No change to props, exported types, or call-site API. `index.tsx` (the only driver-app consumer) is unaffected.
- Interacts with: the playback ticker's own tween logic (reads `prevTargetRef`/`animatedRegion`/`androidCoord`, all now correctly seeded — no interaction with the ride state machine, WS, or money/wallet paths).
- A first fix into an empty buffer now always snaps rather than gliding — this also covers ordinary first-mount (app cold start) and any other future remount trigger, not just the offline→online case, which is the intended broader fix, not scope creep (the same bug class, same code path).

## 5. User-experience effect

- **Driver-facing only.** A driver's own vehicle icon will now jump instantly to its real position after reconnecting (instead of visibly sliding across the map through buildings/roads) whenever the position has moved since the marker was last rendered.
- Not visible mid-session in any way that changes behavior for a driver who hasn't just gone through an offline→online transition — the ticker's ordinary tick-by-tick glide during continuous tracking is completely unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | `ingestFix` now resets on an empty buffer too, not just a distance-triggered one; added an iOS `animatedRegion.setValue()` instant-seed (guarded); `prevTargetRef` now reset on both platforms on this path | Close the gap where the first fix after any remount could never take the instant-snap path |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Added `AnimatedRegion.setValue()` to the `react-native-maps` mock (matches the real library); added a new describe block asserting the instant-seed behavior on both platforms | Regression coverage for this exact fix; the mock was missing a method the real library has |

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

`git revert` is sufficient and complete — this is a pure client-rendering change with no data written anywhere (no DB writes, no Stripe/wallet state, no ride-state changes, no migration). Reverting restores the previous (buggy but not data-affecting) glide behavior. No feature flag: this is a bug fix to existing, already-shipped rendering code on a code path (`ingestFix`) with no external toggle, not a new user-visible feature.

## 9. Verification performed

- [x] Automated tests run: `driver-app/__tests__/components/CarMarker.test.tsx` (27/27 passing, including 2 new tests asserting the instant-seed on both Android and iOS) and `driver-app/__tests__/app/driverDashboardScreen.test.tsx` (54/54 passing — the only other consumer of this component).
- [x] `npx tsc --noEmit` run on the project — no errors attributable to this change.
- [x] Blast-radius grep performed: confirmed `rider-app`'s separate `CarMarker.tsx` copy has the identical gap but was deliberately not touched (out of scope for this fix); confirmed `index.tsx` is the only driver-app consumer of this component.
- [x] Reviewed against CLAUDE.md conventions: no state-machine, money, RLS, or PIPEDA surface touched.
- [ ] Feature-flagged: not applicable — see §8.
- [ ] Manual repro on a real device: **not performed** — see below.

**What was NOT verified:** no real device was available in this environment to reproduce the exact offline→online-after-driving scenario and visually confirm the glide is gone. driver-app has no automated visual-regression tooling (per CLAUDE.md, a known accepted gap for driver-app/rider-app). This fix is reasoned from the actual code paths involved (confirmed via `AnimatedRegion.setValue`'s real implementation in the installed `react-native-maps` source, not assumed) and unit-tested at the exact internal seam (the marker's rendered position immediately after the fix, before any ticker animation runs) — not screenshotted on a device. The ~20s pre-fix delay itself (GPS time-to-first-fix) is unaffected by this change and remains a separate, still-open item.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4 — single file, rider-app's copy explicitly excluded)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: instant snap instead of a cross-map glide, only on reconnect)
