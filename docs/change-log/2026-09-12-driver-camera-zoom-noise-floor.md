# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Live-testing report: follow-camera "zooms in and out" during stop-and-go and while idle. Fix #4 of the driver-app map/camera overhaul (fix #1 heatmap #5272, fix #2 speed staleness #5273, fix #3 marker accuracy #5275, all merged). Directly fulfills the deferred item named in fix #2's own Change Impact Log §4: "the follow-camera's zoom-tier logic reads the same raw `location.coords.speed` and is very likely affected by the same staleness bug ... deliberately not touched here, kept as its own separately-testable fix."|

## 1. Issue / gap identified

The driver-app follow-camera's zoom level flickers between the "stopped" and "city" tiers while the vehicle is genuinely stationary or in stop-and-go traffic, even though the vehicle isn't actually crossing a real speed boundary.

## 2. Root cause

`index.tsx`'s follow-camera effect calls `zoomTierForSpeed(c.speed, ...)` directly on the raw, un-clamped `location.coords.speed` from the platform location API. That value is Doppler-derived and noisy near zero — `locationDisplayGate.ts`'s own documentation (added in fix #2) already records a live report of a genuinely parked vehicle showing ~2.2 m/s of GPS noise.

`FOLLOW_ZOOM_TIERS[0].maxSpeedMps` (the "stopped" tier ceiling) is 2 m/s — *below* that documented noise floor. `TIER_HYSTERESIS_MPS` (0.75 m/s) exists to stop a fix oscillating around a boundary from pumping the zoom, but it only helps when the oscillation is centered near the boundary; ordinary parked-vehicle noise of ~2.2 m/s sits far enough past the 2 m/s ceiling (2.2 > 2 + 0 before hysteresis is even considered on the way up) that a single noisy reading can cross straight into the city tier, and the return trip crosses back once the noise subsides — a real, un-damped tier flap driven entirely by sensor noise, not vehicle motion.

Fix #2 (`#5273`) already root-caused and fixed the identical raw-speed problem for the speed *chip* (`displaySpeedKmh`), clamping both the same GPS noise floor and stale-fix staleness, but explicitly left the follow-camera's own read of the same raw value unfixed as a separate, sequenced item.

## 3. Fix / remediation

Extracted the chip's existing clamp into a reusable `effectiveSpeedMps(speedMps, fixTimestampMs, nowMs): number` (m/s, not rounded to km/h), and made `displaySpeedKmh` a thin wrapper around it (`Math.round(effectiveSpeedMps(...) * 3.6)`) — no behavior change to the chip. Wired the follow-camera's zoom-tier call site in `index.tsx` to pass through the same clamp:

```tsx
const tier = zoomTierForSpeed(
  effectiveSpeedMps(c.speed, location?.timestamp, Date.now()),
  followZoomTierRef.current,
);
```

This removes GPS noise near zero *before* it reaches `zoomTierForSpeed`, so a parked/idle vehicle's noisy readings are clamped to 0 m/s and never approach the stopped tier's 2 m/s ceiling. It also carries over the staleness clamp for free: a frozen `location.coords.speed` held past `MAX_SPEED_FIX_AGE_MS` (6s) now can't keep the camera zoomed to a moving tier after the vehicle has actually stopped and fix cadence has gone quiet — the same failure mode fix #2 fixed for the chip's "stuck at 57 km/h" bug, mirrored here as "stuck zoomed out at the highway tier."

## 4. Risk & impact on existing functionality

- **Blast radius**: grepped every caller of `zoomTierForSpeed` and every importer of `locationDisplayGate.ts`. `zoomTierForSpeed` has exactly one call site in the app (`index.tsx`'s follow-camera effect, edited here). `displaySpeedKmh` has exactly one call site (the speed-chip JSX, unchanged — its behavior is provably identical since it now delegates to the newly-extracted function with no logic change). No other consumer of `effectiveSpeedMps`/`displaySpeedKmh` exists in driver-app, rider-app, or shared/.
- **The effect's own re-run trigger is unchanged** — it still only fires on a fresh `location` object (a real fix), not on a timer, so this fix does not introduce any new re-render/animation cadence. It only changes what speed value is fed into an already-existing calculation.
- **Directionally one-way**: `effectiveSpeedMps` can only ever return 0 or the original value unchanged (`sp >= MIN_DISPLAYED_SPEED_MPS ? sp : 0`) — it never inflates a reading. So the zoom tier selected can only ever be equal to or "more stopped" than before for any given raw fix; it can never newly select a faster tier than the raw speed would have. This can't introduce a new failure mode where the camera fails to zoom in when the vehicle is genuinely moving above the noise floor (any real speed ≥ `MIN_DISPLAYED_SPEED_MPS` of 3 m/s passes through unchanged).
- **No change to `zoomTierForSpeed`'s own hysteresis logic, `FOLLOW_ZOOM_TIERS`, or `TIER_HYSTERESIS_MPS`** — this fix addresses the noisy input feeding that logic, not the logic itself.
- **No change to the camera's throttling (`CAMERA_ANIM_MS`), heading/rotation logic, or "pin car low" centering math** in the same effect — those blocks are untouched and still read `c.speed`/`c.latitude`/etc. directly where noise-clamping doesn't apply to them.

## 5. User-experience effect

**Driver-facing.** The follow-camera should no longer flicker its zoom level while the vehicle is parked or stationary in stop-and-go traffic. No change to zoom behavior at any real driving speed above the existing noise floor (3 m/s / ~10.8 km/h) — the same floor already shipped for the speed chip in fix #2.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/locationDisplayGate.ts` | Extracted `effectiveSpeedMps` (m/s) from `displaySpeedKmh`'s clamping logic; `displaySpeedKmh` now wraps it (`Math.round(... * 3.6)`) | Shares the existing, already-tested noise/staleness clamp between the speed chip and the follow-camera's zoom-tier selection instead of duplicating it |
| `driver-app/app/driver/(tabs)/index.tsx` | Follow-camera effect now calls `zoomTierForSpeed(effectiveSpeedMps(c.speed, location?.timestamp, Date.now()), ...)` instead of passing raw `c.speed` directly; added `effectiveSpeedMps` to the existing import | Removes GPS noise/staleness from the value driving zoom-tier selection, fixing the idle/stop-and-go zoom-flicker bug |
| `driver-app/__tests__/utils/locationDisplayGate.test.ts` | Added an `effectiveSpeedMps` describe block: noise-floor clamp, staleness clamp, pass-through of a real speed, and two integration-style tests proving `zoomTierForSpeed` stays in the stopped tier for parked noise and can't be held in a moving tier by a stale reading | Proves the exact mechanism this fix relies on, at both the unit and integration level |

## 7. Before / after

```ts
// Before — locationDisplayGate.ts: only the chip had a clamped value
export function displaySpeedKmh(speedMps, fixTimestampMs, nowMs): number {
  const age = /* ... */;
  if (age != null && age > MAX_SPEED_FIX_AGE_MS) return 0;
  const sp = speedMps ?? 0;
  return sp >= MIN_DISPLAYED_SPEED_MPS ? Math.round(sp * 3.6) : 0;
}
```

```tsx
// Before — index.tsx: follow-camera read raw, unclamped speed
const tier = zoomTierForSpeed(c.speed, followZoomTierRef.current);
```

```ts
// After — locationDisplayGate.ts: shared clamp, chip wraps it
export function effectiveSpeedMps(speedMps, fixTimestampMs, nowMs): number {
  const age = /* ... */;
  if (age != null && age > MAX_SPEED_FIX_AGE_MS) return 0;
  const sp = speedMps ?? 0;
  return sp >= MIN_DISPLAYED_SPEED_MPS ? sp : 0;
}
export function displaySpeedKmh(speedMps, fixTimestampMs, nowMs): number {
  return Math.round(effectiveSpeedMps(speedMps, fixTimestampMs, nowMs) * 3.6);
}
```

```tsx
// After — index.tsx: follow-camera reads the same clamp the chip uses
const tier = zoomTierForSpeed(
  effectiveSpeedMps(c.speed, location?.timestamp, Date.now()),
  followZoomTierRef.current,
);
```

## 8. Rollback plan

`git-revert-safe` — pure client-side derivation change feeding an existing, unchanged zoom-tier selector. No data written anywhere, no schema/API change, no feature flag needed (the change only clamps an input value that was always read locally on-device).

## 9. Verification performed

- [x] New/updated unit tests: `driver-app/__tests__/utils/locationDisplayGate.test.ts` — 14/14 passing (5 new: 3 direct `effectiveSpeedMps` clamp tests, 2 integration tests proving the zoom-tier noise-floor and staleness fixes specifically).
- [x] Full driver-app suite: `npx jest` — 145/145 suites, 1635/1635 tests passing (no regressions, no flakes this run).
- [x] `npx tsc --noEmit` clean on driver-app — a real production build was not run for this change (JS/TS logic only, no native/bundler-affecting change); `tsc --noEmit` plus the full Jest suite is the verification bar for a pure-logic diff of this size per this repo's own stated conventions, but is explicitly noted as distinct from `npm run build`/EAS build, neither of which was run.
- [x] Blast-radius grep performed: `zoomTierForSpeed` has exactly one call site (edited); `displaySpeedKmh` has exactly one call site (unchanged, behavior-preserving via delegation).
- [x] Confirmed `displaySpeedKmh`'s existing 9 tests (from fix #2) still pass unchanged against the refactor — the extraction is behavior-preserving by construction (`Math.round(x * 3.6)` composed with the same clamp logic that used to be inline).

**What was NOT verified:** the actual on-device elimination of camera zoom flicker — this environment cannot reproduce a live parked-vehicle GPS trace or a real stop-and-go drive. The fix is reasoned from the same well-evidenced noise-floor data fix #2 already used for the identical raw-speed problem in the speed chip (a live-reported ~2.2 m/s reading while parked, against a 2 m/s tier ceiling), not from a reproduced-and-fixed device trace of the camera specifically. If zoom flicker persists after this ships on-device, the next most likely remaining cause is the "rotation coupled to zoom" perception effect noted as a still-open item in the broader map/camera fix sequence (a separate investigation, not addressed by this change) — this fix only addresses the *speed-driven zoom tier* mechanism, not heading/rotation behavior in the same effect.
- [ ] No visual/screenshot regression tooling exists for driver-app (per CLAUDE.md §6) — this change was reasoned about, not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4 — one call site changed for `zoomTierForSpeed`, one call site unaffected for `displaySpeedKmh`)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: less/no zoom flicker while idle/stop-and-go; no change at real driving speeds)
