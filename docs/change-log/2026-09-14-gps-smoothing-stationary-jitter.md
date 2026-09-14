# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code |
| Surface(s) | driver-app, rider-app (shared `shared/utils/gpsSmoothing.ts`) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Item #1 of the 2026-09-14 car-marker latency audit's follow-up round (user-reported: icon rotates back and forth while the phone sits stationary) |

## 1. Issue / gap identified

The driver-app's car marker visibly rotates back and forth while the phone is stationary (screenshots: driver's own "Drive" tab, vehicle parked, `0 km/h`). Confirmed against code, not assumed.

## 2. Root cause

`shared/utils/gpsSmoothing.ts`'s `smoothFix` (a 1-D Kalman filter that runs on every raw GPS fix before it reaches the playback buffer) always used `DEFAULT_PROCESS_NOISE_MPS = 6` — a value deliberately tuned loose so the filter doesn't lag a real accelerating/turning car. That same looseness lets ordinary GPS jitter (multipath, weak sky view — worse indoors/near buildings, exactly a parked-on-a-table scenario) drag the smoothed position several metres per fix, in an essentially random direction each time. `shared/utils/markerPlayback.ts`'s `MIN_SEGMENT_MOVE_M` (2m) then reads any such drag as a real directional segment and assigns it a bearing — the icon spins because consecutive noisy segments point in different directions.

**A related but separate mechanism was investigated and ruled out.** The original hypothesis (this session's own earlier research, before this fix) was that real device-reported GPS accuracy was being silently dropped before reaching `smoothFix`, due to `ingestFix`'s `rawCoord` object only carrying `{latitude, longitude}`. That IS true structurally, but it turned out not to matter: both `CarMarker.tsx` forks already have a deliberate, documented `trackingV2`-gated path that forwards real accuracy (`fix.accuracyM ?? trackingOptionsRef.current.fixAccuracyM`) *around* `rawCoord`, added after a prior incident (accuracyM introduced unconditionally caused iOS's typically-large reported accuracy, 30-65m, to inflate Kalman measurement variance so much the filter lagged badly enough to render the car sideways — "overnight regression" per the code's own comment). `trackingV2` defaults `false` and is not set by any current caller (`useDriverDashboard.ts`, `(tabs)/index.tsx`) — real accuracy is deliberately never used today, pending device-validated re-tuning of `DEFAULT_PROCESS_NOISE_MPS`/`DEFAULT_ACCURACY_M` together. An initial attempt to "fix" the `rawCoord` truncation in this session was reverted once this was discovered — it would have bypassed the `trackingV2` gate and reintroduced the exact prior regression for every driver, not just the intended opt-in population. See §10.

## 3. Fix / remediation

`smoothFix` now derives an implied speed from the running smoothed estimate to each new raw fix, and uses a much smaller process-noise value (`STATIONARY_PROCESS_NOISE_MPS = 1.5`) whenever that implied speed is below `STATIONARY_SPEED_THRESHOLD_MPS = 2.0`. This does not touch accuracy at all — it only changes how fast the filter's `predictedVariance` grows between fixes when the fix stream itself looks parked/idling, so it doesn't interact with the `trackingV2` gate or its documented regression history.

The 2.0 m/s threshold was chosen by direct measurement against this module's own formula (not guessed): a synthetic 8-fix adversarial jitter sequence's worst per-tick segment drops from 3.44m (old fixed 6 m/s noise) to 1.74m — under `MIN_SEGMENT_MOVE_M` (2m), so `markerPlayback.ts` stops assigning it a spurious bearing — while a synthetic steady 2.5 m/s crawl only picks up ~0.2m of extra lag over 20s. Pushing the threshold higher (2.5-3 m/s) stopped helping the jitter case further while continuing to cost the crawl case more lag, so 2.0 is the measured knee of that tradeoff, not a round-number guess.

**`markerPlayback.ts`'s `MIN_SEGMENT_MOVE_M` was deliberately left unchanged.** The original plan (from this session's own prior research turn) was to tighten it once the source-level jitter fix landed. Once the jitter fix above was measured, the adversarial case already lands safely under the *existing* 2m threshold with margin — there is no verified evidence a tighter threshold is still needed, and raising it without real device data risks exactly what was flagged as the danger: masking slow real turns. Not changing it is the reasoned outcome of measurement, not an omission.

## 4. Risk & impact on existing functionality

- **Blast radius: shared module, both apps.** `smoothFix` is called from exactly two call sites (`driver-app/components/CarMarker.tsx` and `shared/components/CarMarker.tsx`'s `ingestFix`), both grepped and confirmed. No other consumer exists.
- **Does not touch the `trackingV2`/accuracy-gating logic at all** — verified by reading both call sites' full `smoothFix(...)` invocations, including the gated `accuracyM` spread that runs after `rawCoord`. This fix only changes the `processNoiseMps` selection inside `smoothFix` itself.
- **Does not change `MIN_SEGMENT_MOVE_M`, `selectBearing`, `coalescePlaybackBearing`, or any of the Android-Auto-specific `courseReference`/`carSurface.tsx` logic landed by a concurrent session today** (`docs/change-log/2026-09-14-android-auto-tracking-v2.md`, commits `a6835daa9`/`8b7d589ea`/`74d50137f`/`626a4f51d`, already merged to `main`) — confirmed no file overlap: that work touches `carFixChannel.ts`, `carLocationTask.ts`, `useCarLocation.ts`, `backgroundLocation.ts`, `carSurface.tsx`, and adds the `courseReference` prop to both `CarMarker.tsx` forks; this fix touches only `gpsSmoothing.ts`.
- **Interaction with a genuinely slow-moving vehicle**: below 2.0 m/s implied speed, the filter now trusts each new fix less (lower Kalman gain) than before. Measured cost: ~0.2m of additional lag over 20s at 2.5 m/s in the synthetic test above. A vehicle that is genuinely creeping at 1-2 m/s (e.g., crawling in a parking lot) will see very slightly more smoothing lag than before — judged an acceptable tradeoff given the measured jitter improvement, but not validated on a real device.

## 5. User-experience effect

Driver-facing, visible mid-session to any driver whose app is rendering the live car marker (online, on a trip, or Android Auto's phone-screen-fed path — not the head-unit's own `carSurface.tsx` ingest, which is a separate pipeline untouched here). The marker should wobble/rotate less while genuinely stationary. No UI, copy, or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/utils/gpsSmoothing.ts` | Added `STATIONARY_SPEED_THRESHOLD_MPS`/`STATIONARY_PROCESS_NOISE_MPS`; `smoothFix` derives implied speed and scales process noise down when below threshold | Damps GPS jitter at the source when the vehicle reads as parked, without touching accuracy handling |
| `driver-app/__tests__/gpsSmoothing.test.ts` | Two new tests: adversarial stationary-jitter sequence (asserts max per-tick segment `< 2m`, matching `MIN_SEGMENT_MOVE_M`), and a steady 2.5 m/s crawl (asserts it isn't mistaken for parked) | Proves the fix against the actual downstream-relevant metric, and guards the opposite failure mode |

## 7. Before / after

```typescript
// Before
const predictedVariance = state.variance + dtSec * processNoiseMps * processNoiseMps;
```

```typescript
// After
const impliedSpeedMps =
  dtSec > 0 ? distanceMeters(state.latitude, state.longitude, fix.latitude, fix.longitude) / dtSec : 0;
const effectiveProcessNoiseMps =
  impliedSpeedMps < STATIONARY_SPEED_THRESHOLD_MPS
    ? Math.min(processNoiseMps, STATIONARY_PROCESS_NOISE_MPS)
    : processNoiseMps;
const predictedVariance = state.variance + dtSec * effectiveProcessNoiseMps * effectiveProcessNoiseMps;
```

## 8. Rollback plan

`git-revert-safe` — pure function change in one shared utility module, no schema/migration/flag involved, no persisted state. Reverting restores the previous fixed-process-noise behavior with no other side effect.

## 9. Verification performed

- [x] New unit tests (both described above) — pass.
- [x] Full `gpsSmoothing.test.ts` re-run: 14/14 passing (12 pre-existing + 2 new).
- [x] Broader regression sweep, both apps: `gpsSmoothing markerPlayback CarMarker vehicleTracking` — driver-app 43/43 passing, rider-app 78/78 passing.
- [x] `npx tsc --noEmit` clean in both `driver-app` and `rider-app` (this module is shared).
- [x] Blast-radius grep: exactly 2 call sites of `smoothFix` repo-wide, both read and confirmed unaffected in their `trackingV2`/accuracy-gating logic.
- [x] Confirmed no file-level overlap with the concurrent same-day Android Auto heading/course-reference work already merged to `main`.
- [ ] `spinr-edge-case-reviewer` run against this diff — pending at commit time; any finding lands as a follow-up commit before merge.

**What was NOT verified:** no real device was available this session (same gap already tracked for this feature area) — the 2.0 m/s threshold and 1.5 m/s stationary process-noise value are measured against this module's own formula using synthetic fixtures, not against a real phone's actual GPS noise floor. `gpsSmoothing.ts`'s own existing comments already flag `DEFAULT_ACCURACY_M`/`DEFAULT_PROCESS_NOISE_MPS` as "not tuned against live device data" for the same reason — this fix inherits that same limitation for its two new constants. No visual-regression tooling exists for driver-app or rider-app (repo-wide, not specific to this fix), so the on-screen effect was reasoned about via the segment-distance metric, not screenshotted.

## 10. Alternatives considered

- **Turn on `trackingV2`/real accuracy plumbing globally** (the original plan for this fix, before investigation): rejected once discovered that this path already exists, is deliberately gated off, and has a documented prior "rendered sideways overnight" regression tied to un-re-tuned `DEFAULT_PROCESS_NOISE_MPS`. Doing this properly needs real-device-calibrated re-tuning of the accuracy/process-noise pair together, which is exactly the device-access gap already tracked elsewhere in this project. Flipping it on unilaterally in this session would have been reckless given that history.
- **Direction-consistency-based damping instead of a single-sample speed threshold** (e.g., requiring N consecutive same-direction fixes before trusting movement, mirroring `markerPlayback.ts`'s own decel-ratio logic): more robust in principle — a single new fix cannot truly distinguish incoherent jitter from genuine slow movement by magnitude alone, only a threshold's proxy for it. Rejected for this round as materially larger scope (new multi-sample state, not a one-parameter change) for a fix that already measures well against the adversarial case tried; worth revisiting if real device data later shows the 2.0 m/s threshold isn't sufficient.
- **Raise `MIN_SEGMENT_MOVE_M` instead of/in addition to fixing the source**: rejected — see §3. The source-level fix already gets the adversarial case under the existing threshold; raising the downstream gate too would be the "blind" tuning explicitly flagged as risky (masking slow real turns) without evidence it's still needed.
