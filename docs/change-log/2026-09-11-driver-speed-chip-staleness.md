# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Live-testing report: speed chip stuck at 57 km/h for minutes after the vehicle stopped, before resetting to 0 |

## 1. Issue / gap identified

The driver dashboard's on-map speed readout ("speed chip") sometimes holds a stale, non-zero value (e.g. 57 km/h) for minutes after the vehicle has actually stopped, before eventually resetting to 0.

## 2. Root cause

The speed chip reads `location.coords.speed` directly from the raw `Location.LocationObject` state. That state only updates when a **new** GPS fix arrives — and `watchPositionAsync`'s `distanceInterval` gate (`LOCATION_CONFIGS` in `useDriverDashboard.ts`) means Android's location provider goes fully quiet at a genuine standstill (no fix, because the device hasn't moved the configured distance). With no new fix, `location.coords.speed` simply holds whatever the last real reading was — indefinitely, until the driver eventually moves enough to trigger a fresh fix. This is the exact same underlying GPS-quiet-at-a-standstill behavior already diagnosed and fixed for the car *marker* itself (`MARKER_HEARTBEAT_MS` re-emit in `useDriverDashboard.ts`, 2026-08-30) — but that fix only feeds the marker's own playback buffer, not the `location` state the speed chip reads, so the chip was never covered by it.

## 3. Fix / remediation

Added `displaySpeedKmh(speedMps, fixTimestampMs, nowMs)` to `driver-app/utils/locationDisplayGate.ts` — a pure, unit-tested helper that clamps the displayed value to 0 when the underlying fix is older than a new `MAX_SPEED_FIX_AGE_MS` (6s) threshold, on top of the pre-existing near-zero noise floor (`MIN_DISPLAYED_SPEED_MPS`). Wired it into the speed chip in `index.tsx`, replacing the previous inline noise-floor-only clamp.

Because `location` itself doesn't change while no new fix arrives, a staleness clamp alone wouldn't actually *fire* on a timer — it would only be re-evaluated the next time something else happens to re-render the screen. Added a small dedicated 1-second re-render tick (`speedTick` state, online-only) so the clamp is re-checked every second regardless of fix arrival, matching the "reads as a stop within a couple of ticks" intent rather than waiting on an unrelated re-render.

## 4. Risk & impact on existing functionality

- **Blast radius**: grepped every reader of `MIN_DISPLAYED_SPEED_MPS`/`locationDisplayGate.ts` — only `index.tsx`'s speed chip and the follow-camera zoom-tier logic (`zoomTierForSpeed`, unchanged by this fix) consume this module. No other screen or component reads the speed chip's value; it's a `pointerEvents="none"` display-only overlay.
- **What else reads `location.coords.speed`?** Grepped `driver-app/`: the follow-camera zoom-tier effect (`zoomTierForSpeed(location.coords.speed, ...)`) also reads it directly, and is **not** changed by this fix — it's the next item in this initiative's sequence (zoom-flicker-during-stop-and-go is very likely the same staleness bug manifesting as a frozen/wrong zoom tier, but that's a separate, distinct code path from the chip and gets its own fix and its own device verification pass rather than being bundled in here).
- **New 1s interval**: added only while `isOnline`, cleared on unmount/offline — matches the existing pattern of this file's other online-only intervals (unread-notification poll, surge-multiplier poll). Negligible CPU cost (a state increment, no I/O).
- **No change to the underlying GPS/location pipeline** — `LOCATION_CONFIGS`, `watchPositionAsync`, and the marker's own heartbeat are all untouched. This is purely a display-layer clamp.

## 5. User-experience effect

**Driver-facing.** The speed chip should now read 0 within a few seconds of the vehicle genuinely stopping, instead of holding the last real speed for minutes. No change to the chip's behavior while actually driving (a moving vehicle's fixes arrive well under the 6s staleness window at every `LOCATION_CONFIGS` cadence, so the clamp never engages during real motion).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/locationDisplayGate.ts` | Added `MAX_SPEED_FIX_AGE_MS` and `displaySpeedKmh()` | Pure, testable staleness + noise-floor clamp for the speed chip |
| `driver-app/app/driver/(tabs)/index.tsx` | Speed chip now calls `displaySpeedKmh(...)`; added a 1s `speedTick` re-render timer (online-only) | Wire the new clamp in; make it actually re-evaluate on a timer, not only on new fixes |
| `driver-app/__tests__/utils/locationDisplayGate.test.ts` | New file — 9 tests for `displaySpeedKmh` plus a smoke check on the pre-existing exports | No test coverage existed for this module at all before this change |

## 7. Before / after

```tsx
// Before — no staleness handling; holds the last real fix's speed forever
{(location.coords.speed ?? 0) >= MIN_DISPLAYED_SPEED_MPS
  ? Math.round((location.coords.speed ?? 0) * 3.6)
  : 0}
```

```tsx
// After — clamps to 0 once the fix is stale, re-evaluated every second
{displaySpeedKmh(location.coords.speed, location.timestamp, Date.now())}
```

## 8. Rollback plan

`git-revert-safe` — pure client-rendering/display-clamp change, no data written anywhere, no schema/API change.

## 9. Verification performed

- [x] New unit tests: `driver-app/__tests__/utils/locationDisplayGate.test.ts` — 9/9 passing (fresh speed, noise-floor clamp, staleness clamp, boundary condition, missing-timestamp back-compat, null/negative-speed handling).
- [x] Full driver-app suite: `npx jest` — 142/142 suites, 1606/1606 tests passing (one unrelated test — `backgroundMessaging.android.test.ts` — flaked once when run as part of the full suite, passed both in isolation and on a full-suite re-run; confirmed unrelated to this change before treating it as a flake, not investigated further).
- [x] `npx tsc --noEmit` on driver-app — clean, no errors.
- [x] Blast-radius grep performed: confirmed only the speed chip and the (unmodified) zoom-tier logic read `location.coords.speed`/this module.
- [ ] **Not verified on a real device** — the exact "stuck at 57, resets after minutes" scenario requires a real GPS standstill on real hardware, which this environment doesn't have.

**What was NOT verified:** the actual on-device timing (does it really read 0 within ~6-7s of a real stop, as designed) — this environment cannot simulate `watchPositionAsync` going quiet at a standstill. The 6s threshold is a judgment call (comfortably above every `LOCATION_CONFIGS` timeInterval so a genuinely-moving vehicle's normal fix cadence never falsely triggers it) rather than a value tuned against real device telemetry — the user reporting this bug will be the first real-world check via the OTA test loop. The related zoom-flicker symptom (same root cause, different code path — the follow-camera's zoom-tier logic) is explicitly NOT touched by this fix and is next in this initiative's sequence.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer cleanup needed)
- [x] Blast radius is stated, not assumed (grepped, listed in §4 — chip and zoom-tier logic, only the chip changed)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the only visible effect: faster clamp-to-zero after a real stop)
