# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code |
| Surface(s) | driver-app (`components/CarMarker.tsx`); shared `components/CarMarker.tsx` also used by rider-app (ported for parity) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Live-testing report: "the vehicle is facing east" after the speed shown reset to 0. Fix #6 of the driver-app map/camera overhaul sequence (fix #1 heatmap #5272, fix #2 speed staleness #5273, fix #3 marker accuracy #5275, fix #4 zoom-tier noise floor #5278, fix #5 online/offline camera framing #5281, all merged). |

## 1. Issue / gap identified

After certain resets of the car marker's playback buffer (not just a genuine first-ever mount — also a real GPS position jump, e.g. after an offline→online remount, a ride ending, or a background/tunnel gap), the marker can briefly snap to a wrong heading — reported live as "facing east" — before real movement re-establishes the correct direction.

## 2. Root cause

`CarMarker.tsx`'s bearing-selection chain (`selectBearing()` in `shared/utils/vehicleTracking.ts`) already demotes the raw platform-reported GPS heading below movement-derived bearings, specifically because that raw value is known-unreliable across platforms: iOS reports `-1` for "invalid" (already filtered), but **Android's `Location.getBearing()` returns a literal `0.0` when its own `hasBearing()` flag is false** — indistinguishable from a car genuinely driving due north. `hasMovementBearingRef` exists specifically to close this hole: once movement has ever established a real bearing, the raw heading is ignored entirely, no matter what value it reports.

However, `CarMarker.tsx`'s `ingestFix()` cleared `hasMovementBearingRef.current = false` unconditionally on **any** buffer reset — both `isFirstFix` (a genuine first-ever fix, where there is nothing to trust yet) and `shouldResetBuffer` (a real distance jump past `SNAP_DISTANCE_M`, where only the **position** is known-stale, not the vehicle's already-established sense of direction). Every jump reset therefore re-opened the exact placeholder-heading window `hasMovementBearingRef` was built to shut — for one or more ticks, until real movement (≥3m) accumulated again, a stale/garbage/placeholder raw heading could win and snap the icon to a wrong direction.

## 3. Fix / remediation

Gated the `hasMovementBearingRef.current = false` reset on `isFirstFix` alone, in both `CarMarker.tsx` copies:

```ts
if (isFirstFix) {
  hasMovementBearingRef.current = false;
}
```

A jump-triggered reset (`shouldResetBuffer`, `isFirstFix` false) now leaves `hasMovementBearingRef` untouched. Practical effect for the narrow "parked / sub-3m-jitter, right after a jump" window: `selectBearing()` returns `{bearing: null, source: 'none'}` instead of trusting the raw heading, so the ticker applies no rotation that tick — the icon **freezes at its last known bearing** rather than snapping to a possibly-wrong guess. Once real movement resumes (≥3m in one tick), the route/travel branches take over immediately and unconditionally (they do not depend on `hasMovementBearingRef` at all), so this only affects the brief window before movement resumes, never ongoing driving.

Ported the identical one-line change (plus the same updated doc comments) to `shared/components/CarMarker.tsx`, which rider-app uses to render the assigned driver's marker and has the exact same buffer-reset structure (confirmed via grep: identical `isFirstFix`/`shouldResetBuffer` block, identical `hasMovementBearingRef` mechanism) — rider-app remounts this component on every ride-phase screen transition (ride-options → driver-arriving → … → ride-in-progress), which is its own equivalent of driver-app's remount/jump triggers.

**Judgment call, stated explicitly**: this reverses a previously-intentional design decision. The prior code's own comment reasoned "the old direction says nothing about the new location" as justification for clearing the latch on every reset. That's true, but the fallback it enables (trusting the raw platform heading) is the exact unreliable source `hasMovementBearingRef` exists elsewhere to reject — so between "freeze at the last known bearing" and "guess from a value known to sometimes be a placeholder," freezing is the safer default. This is reasoned from the code's own extensively-documented history of exactly this heading-placeholder failure mode (see `selectBearing()`'s doc comment, itself added after two prior live-testing incidents), not a redesign — but it is a real behavior change worth a human's second look.

## 4. Risk & impact on existing functionality

- **Blast radius**: grepped every reader/writer of `hasMovementBearingRef` in both files — it is set in exactly two places per file (the reset block just changed, and the "movement established a bearing" set-to-true inside the ticker, unchanged) and read in exactly one place (the `selectBearing()` call, unchanged). No other component or hook reads this ref; it's fully private to each `CarMarker` instance.
- **Does not touch `selectBearing()`, `coalescePlaybackBearing()`, `isImplausibleJump()`, `shouldResetBuffer()`, or any other part of the bearing/position pipeline** — this is a one-line gating change on when a single ref gets cleared.
- **Directionally conservative**: the only behavior this can change is whether a raw platform heading is trusted immediately after a jump-triggered reset. It can never cause a *new* wrong-heading snap that didn't exist before (the old behavior); at worst, in the rare case where the raw heading actually *was* correct right after a jump, the icon now freezes for a moment longer than it would have — a strictly safer failure mode than the reported "facing east" symptom.
- **No change to position/smoothing state** — `bufferRef`, `smoothingStateRef`, `prevTargetRef`, and the platform-specific instant-seed logic in the same reset block are all unconditional and untouched.
- **Parity maintained**: both `CarMarker.tsx` copies (driver-app's own, and the shared one rider-app uses) now have identical reset-gating logic, closing a gap that existed since the underlying `hasMovementBearingRef` mechanism was first added to both files.

## 5. User-experience effect

**Driver-facing and rider-facing** (rider-app shows the driver's marker during a ride). The car icon should no longer briefly snap to a wrong heading (e.g. "facing east") immediately after a large position jump once the marker has already established a real direction of travel. No change to bearing behavior during ongoing normal driving, nor on a genuine first-ever mount (cold start), where the raw-heading fallback still applies exactly as before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/CarMarker.tsx` | Gated `hasMovementBearingRef.current = false` on `isFirstFix` alone (was unconditional inside the combined `isFirstFix \|\| shouldResetBuffer` reset block); updated two doc comments to match | Stops a jump-triggered reset from re-opening the known-unreliable raw-heading fallback that a genuine first-ever mount still legitimately needs |
| `driver-app/__tests__/components/CarMarker.test.tsx` | Switched `pushFix`/`shouldResetBuffer` in the module mock from permanent no-ops to the real implementations (wrapped in `jest.fn()` so calls are still trackable) — needed so `bufferRef.current` genuinely accumulates and `isFirstFix` can actually turn false in a test; added a new describe block with a regression test proving the exact mechanism (established bearing survives a jump-reset; a "wrong" raw heading afterward is not applied) | Proves the fix; verified to fail against the pre-fix code (temporarily reverted and re-tested) before restoring |
| `shared/components/CarMarker.tsx` | Identical fix + doc comment updates, ported for parity | Rider-app's copy has the same mechanism and the same bug |
| `rider-app/__tests__/carMarkerPositionChange.test.tsx` | Same mock-switch + an equivalent regression test — reads the rendered `Marker`'s `rotation` prop (Android path) instead of an `onBearingChange` callback, since the shared component has no such callback (driver-app-only prop) | Proves the same fix in the file rider-app's test suite actually exercises |

## 7. Before / after

```ts
// Before (both CarMarker.tsx copies)
if (isFirstFix || shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
    bufferRef.current.length = 0;
    smoothingStateRef.current = null;
    hasMovementBearingRef.current = false; // cleared on EVERY reset
    // ...
}
```

```ts
// After
if (isFirstFix || shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
    bufferRef.current.length = 0;
    smoothingStateRef.current = null;
    if (isFirstFix) {
        // only a genuine first-ever fix re-opens the raw-heading fallback
        hasMovementBearingRef.current = false;
    }
    // ...
}
```

## 8. Rollback plan

`git-revert-safe` — pure client-side bearing-selection input change, no data written anywhere, no schema/API change. Reverting restores the exact prior (also live-tested) behavior.

## 9. Verification performed

- [x] New regression tests, both apps: `driver-app/__tests__/components/CarMarker.test.tsx` (new describe block, 1 test) and `rider-app/__tests__/carMarkerPositionChange.test.tsx` (new describe block, 1 test) — both **manually confirmed to fail against the pre-fix code** (temporarily reverted the production change via `git stash`, re-ran, observed the exact "facing east" failure — `onBearingChange`/`rotation` called with `90` — then restored the fix and re-ran green) before being finalized, per this repo's own bar for a meaningful (non-vacuous) regression test.
- [x] Full driver-app suite: `npx jest` — 145/145 suites, 1645/1645 tests passing. (One run hit the same pre-existing, unrelated `backgroundMessaging.android.test.ts` flake seen in fixes #2 and #4; confirmed via a clean full-suite re-run.)
- [x] Full rider-app suite: `npx jest` — 149/149 suites, 2058/2058 tests passing.
- [x] `npx tsc --noEmit` clean on both driver-app and rider-app.
- [x] Blast-radius grep performed on `hasMovementBearingRef` in both files: exactly 2 writers, 1 reader, both fully private to the component instance — no other consumer.

**What was NOT verified:** the actual on-device elimination of the wrong-heading snap — this environment cannot reproduce a live GPS jump (e.g. a real offline→online transition with a genuine position change, or a real background/tunnel gap) on a device. The fix is reasoned from the same well-documented, already-tested bearing-priority mechanism this codebase built specifically to reject unreliable raw headings (see `selectBearing()`'s own doc comment and its two cited prior live-testing incidents), and is directionally conservative (see §4) — not from a reproduced-and-fixed device trace. This is also, as noted in §3, a judgment call reversing a previously-intentional design choice; if a reviewer disagrees with the "freeze rather than guess" tradeoff, that's the one line to revisit. No visual-regression tooling exists for driver-app or rider-app (per CLAUDE.md), so the on-screen icon behavior itself was reasoned about and unit-tested at the callback/prop level, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4 — exactly 2 writers/1 reader of the changed ref, fully private per component instance)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: no more brief wrong-heading snap after a jump reset, once movement had already established a direction)
