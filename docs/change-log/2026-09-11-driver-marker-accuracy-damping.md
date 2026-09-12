# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code |
| Surface(s) | driver-app (shared `gpsSmoothing.ts`/`fixFeed.ts` also read by rider-app) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Live-testing report: the driver's own car icon sometimes visibly "travels" across the map while the vehicle is genuinely parked/stationary, then snaps back. Fix #3 of the driver-app map/camera overhaul (fix #1 heatmap, fix #2 speed staleness, both merged: #5272, #5273). |

## 1. Issue / gap identified

While parked/stationary, the driver's own vehicle marker sometimes animates a visible glide away from its real position, then corrects back — even though the vehicle never moved.

## 2. Root cause

Every raw GPS fix passes through a Kalman-style smoothing filter (`shared/utils/gpsSmoothing.ts`'s `smoothFix()`) before it reaches the marker's playback buffer. That filter already accepts an `accuracyM` parameter — a real GPS accuracy value should widen the fix's measurement variance and make the filter trust (and animate toward) that fix *less*. But neither ingest path into the marker (the `MarkerFix` feed, nor the coordinate prop) has ever actually supplied a real accuracy value — the module's own prior doc comment on `DEFAULT_ACCURACY_M` explicitly names this as a known, deliberate gap ("neither of CarMarker's two ingest paths currently plumbs expo-location's reported accuracy through... pass `accuracyM` explicitly once it does").

Practical effect: every fix is smoothed as if it had the same fixed, middling accuracy (8m), regardless of how imprecise it actually was. A parked vehicle near buildings, underground parking, or with a weak sky view commonly produces a real GPS fix with poor accuracy (20-40m+) that drifts a real, moderate distance (10-30m) from the true position — too small to trip the existing "reject an impossible jump" guard (`isImplausibleJump`, tuned to catch multi-hundred-metre teleports, not ordinary multipath drift), but large enough for the smoothing filter — blind to how unreliable that specific fix actually was — to visibly animate toward it, then correct back once a better fix arrives.

## 3. Fix / remediation

Threaded the real, already-available GPS accuracy value through the pipeline that already knows how to use it:

- Added `accuracyM?: number | null` to the shared `MarkerFix` interface (`shared/utils/fixFeed.ts`).
- `driver-app/hooks/useDriverDashboard.ts` now populates `accuracyM: loc.coords.accuracy` on both the live fix (`lastMarkerFixRef` and the marker-feed `emit()` call) and, by inheritance, the existing "stationary heartbeat" re-emit (which spreads `lastMarkerFixRef.current`, so it now carries the field automatically — no separate change needed there).

No change to `smoothFix()`'s own math — it already correctly widens measurement variance (and therefore lowers its trust in the fix) as `accuracyM` grows; this fix only supplies the real value it was always able to use. Both `CarMarker.tsx` copies (driver-app's own, and `shared/components/CarMarker.tsx` which rider-app uses) already spread the entire raw fix object (`{ ...rawCoord, timestampMs: ts }`) into `smoothFix()`, so the new field reaches the filter with zero changes needed to either marker component.

## 4. Risk & impact on existing functionality

- **Blast radius**: `MarkerFix` is a shared type. Grepped every producer/consumer: driver-app's `useDriverDashboard.ts` (changed, the only current real-GPS producer), both `CarMarker.tsx` copies (unchanged — already forward whatever fields a fix object carries), and rider-app's own fix source (the WS-relayed driver position) — which does **not** currently supply `accuracyM` and is unaffected; `smoothFix()` falls back to its existing default exactly as before for any fix that omits the field (covered by a new test).
- **Field is optional**: every existing caller/fixture that doesn't set `accuracyM` continues to compile and behave identically (`smoothFix()`'s fallback to `DEFAULT_ACCURACY_M` is unchanged and now has a dedicated regression test proving parity).
- **No change to the rejection-guard (`isImplausibleJump`) or the playback buffer** — this only changes how much the *smoothing* stage trusts a given fix, not the reset/snap logic already fixed in prior PRs.
- **Directionally, this can only reduce marker movement, never add it**: a real accuracy value only ever *widens* measurement variance relative to the previous fixed default in the worst case (a fix reporting a genuinely poor accuracy), which strictly lowers the Kalman gain (less movement toward that fix); a fix reporting *better* accuracy than the 8m default would move slightly more toward it — the intended, correct behavior for a high-confidence fix, and not the reported failure mode.

## 5. User-experience effect

**Driver-facing.** The driver's own vehicle icon should animate less (or not visibly move at all) in response to an individual low-quality GPS fix while parked, instead of gliding away and snapping back. No change to marker behavior while actually driving with normal-accuracy fixes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/fixFeed.ts` | Added `accuracyM?: number \| null` to `MarkerFix` | Lets a real GPS accuracy value flow through the existing fix pipeline into `smoothFix()`, which already knows how to use it |
| `driver-app/hooks/useDriverDashboard.ts` | `lastMarkerFixRef` and the marker-feed `emit()` call now include `accuracyM: loc.coords.accuracy`; widened `lastMarkerFixRef`'s ref type to match | Supplies the real value at the one place driver-app produces live GPS fixes |
| `driver-app/__tests__/gpsSmoothing.test.ts` | Added 2 tests: a poor-accuracy fix is damped harder than the same jitter reported as good-accuracy; an omitted `accuracyM` behaves identically to the pre-existing default | Proves the exact mechanism this fix relies on, and proves back-compat for producers that don't supply the field |

## 7. Before / after

```ts
// Before — useDriverDashboard.ts always emitted a fix with no accuracy info
markerFixFeedRef.current.emit({
  latitude: loc.coords.latitude,
  longitude: loc.coords.longitude,
  heading: loc.coords.heading,
  timestampMs: loc.timestamp || Date.now(),
});
// smoothFix() always fell back to DEFAULT_ACCURACY_M (8m) for every fix,
// regardless of how accurate or inaccurate it actually was.
```

```ts
// After — the fix's real reported accuracy flows through
markerFixFeedRef.current.emit({
  latitude: loc.coords.latitude,
  longitude: loc.coords.longitude,
  heading: loc.coords.heading,
  accuracyM: loc.coords.accuracy,
  timestampMs: loc.timestamp || Date.now(),
});
// smoothFix() now widens its trust of a poor-accuracy fix accordingly,
// damping a low-quality drifted fix harder than before.
```

## 8. Rollback plan

`git-revert-safe` — pure client-side smoothing-input change, no data written anywhere, no schema/API change, no feature flag needed (the underlying filter math is unchanged; this only supplies a previously-unused optional input to it).

## 9. Verification performed

- [x] New unit tests: `driver-app/__tests__/gpsSmoothing.test.ts` — 12/12 passing (2 new), directly proving the accuracy-based damping mechanism and default-fallback parity.
- [x] Full driver-app suite: `npx jest` — 145/145 suites, 1630/1630 tests passing.
- [x] Full rider-app suite: `npx jest` — 149/149 suites, 2056/2056 tests passing (confirms the shared `MarkerFix` type change doesn't break rider-app's own CarMarker, which doesn't yet supply `accuracyM`).
- [x] `npx tsc --noEmit` clean on both driver-app and rider-app. (A standalone `tsc --noEmit` run inside `shared/` alone shows pre-existing, unrelated errors — confirmed via `git stash` to reproduce identically without this change — an artifact of running that package's tsconfig in isolation, not something either app's real build hits.)
- [x] Blast-radius grep performed: confirmed the only current real-GPS producer into `MarkerFix` is driver-app's own location watch; rider-app's WS-relayed fix source is unaffected and unchanged.

**What was NOT verified:** the actual on-device reduction in ghost-marker movement — this environment cannot reproduce real GPS multipath/accuracy degradation (e.g. parking near a building). The fix is reasoned from the smoothing filter's own well-understood, already-tested Kalman-gain math (a real accuracy value strictly cannot make damping worse, only correct a previously-ignored input), not from a reproduced-and-fixed device trace. If the reporting device still sees ghost movement after this ships, the next-most-likely cause would be a fix whose *reported* accuracy is itself misleadingly good despite real drift (a genuine GPS chipset limitation this fix cannot correct for) — that would need a different mitigation (e.g. a stationary/parked-detection heuristic independent of reported accuracy), not pursued here since there's no evidence yet that it's needed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4 — one producer changed, one consumer type widened, both marker components confirmed to already forward the field)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: less/no ghost movement on a poor-accuracy fix while parked)
