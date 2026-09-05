/**
 * Lightweight, dependency-free GPS position smoothing for the live car
 * marker (driver-app self-tracking; shared so rider-app's driver-tracking
 * marker can opt in later without re-deriving this).
 *
 * A raw GPS fix is noisy (multipath reflections, weak sky view, Doppler
 * speed error) even before it reaches the playback buffer in
 * markerPlayback.ts — that buffer smooths BETWEEN fixes (interpolation,
 * spline), but does nothing to smooth the fixes themselves. This module
 * applies a well-established 1-dimensional Kalman ("recursive least
 * squares") filter per fix, ahead of the playback buffer, so a single noisy
 * fix is damped toward the filter's running estimate instead of being
 * played back verbatim.
 *
 * This is the same lightweight technique widely documented for smoothing
 * consumer GPS traces (e.g. the "Kalman filter for GPS tracks" pattern used
 * by several open-source location-tracking projects): treat position as a
 * random walk with process noise `Q` (how fast the true position can
 * plausibly change, in m/s) and each fix as a noisy measurement with
 * variance `accuracyM^2`. No new dependency, no matrix math needed at 1-D —
 * roughly 100 lines total including this comment.
 *
 * Pure math, no timers or React — same shape as markerPlayback.ts, so the
 * caller (CarMarker.tsx) holds the running `SmoothingState` in a ref and
 * calls `smoothFix` once per ingested fix, before `pushFix`.
 */

import { distanceMeters } from './vehicleTracking';

export interface SmoothingState {
  latitude: number;
  longitude: number;
  /** Running position-estimate variance (metres²). */
  variance: number;
  timestampMs: number;
}

/**
 * Assumed GPS measurement accuracy (metres) when the fix doesn't report one.
 * Neither of CarMarker's two ingest paths (the fixFeed and the coordinate
 * prop) currently plumbs expo-location's reported `accuracy` through to the
 * marker — doing so would mean widening MarkerFix and every fixFeed
 * producer, well beyond a smoothing filter's own scope. This constant is
 * the deliberate middle of locationDisplayGate.ts's own documented range
 * ("Typical GNSS fixes are 3–15 m") until that plumbing exists; pass
 * `accuracyM` explicitly once it does; per this file's own worked example,
 * this is a documented-default choice, not one tuned against live device
 * data (no device/simulator was available to tune it empirically).
 */
const DEFAULT_ACCURACY_M = 8;

/**
 * Process noise: how many m/s of true-position drift the filter allows
 * between fixes before trusting a new measurement fully. Vehicle speeds are
 * far higher than the ~3 m/s default this technique commonly uses for
 * pedestrian tracking (that value would lag noticeably during acceleration
 * or a turn), so this is tuned up for driving; still damps single-fix
 * jitter at a standstill or steady cruise, where consecutive fixes 2-4 s
 * apart shouldn't imply the car actually moved this filter's own multi-
 * metre step.
 */
const DEFAULT_PROCESS_NOISE_MPS = 6;

/**
 * Fold one new fix into the running smoothed estimate. `state === null`
 * seeds the filter at the raw fix (nothing to smooth against yet — smoothing
 * a lone point would just be an arbitrary guess). Call again with the
 * returned state for the next fix; pass `null` (re-seed) after a genuine
 * teleport/reset so the filter doesn't drag the new position back toward
 * where the car used to be.
 */
export function smoothFix(
  state: SmoothingState | null,
  fix: { latitude: number; longitude: number; timestampMs: number; accuracyM?: number | null },
  processNoiseMps: number = DEFAULT_PROCESS_NOISE_MPS,
): SmoothingState {
  const accuracyM =
    fix.accuracyM != null && Number.isFinite(fix.accuracyM) && fix.accuracyM > 0
      ? fix.accuracyM
      : DEFAULT_ACCURACY_M;
  const measurementVariance = accuracyM * accuracyM;

  if (!state) {
    return {
      latitude: fix.latitude,
      longitude: fix.longitude,
      variance: measurementVariance,
      timestampMs: fix.timestampMs,
    };
  }

  // Clamp negative elapsed time (out-of-order/duplicate fix) to 0 rather
  // than letting it shrink the predicted variance — pushFix separately
  // guards ordering for the playback buffer itself, so this just avoids
  // corrupting the filter's own state on the same class of bad input.
  const dtSec = Math.max(0, (fix.timestampMs - state.timestampMs) / 1000);
  const predictedVariance = state.variance + dtSec * processNoiseMps * processNoiseMps;

  // Kalman gain: how much to trust the new measurement vs. the running
  // estimate. Approaches 1 (fully trust the fix) as predictedVariance grows
  // relative to measurementVariance — e.g. after a long gap, or when the
  // measurement itself is unusually precise.
  const gain = predictedVariance / (predictedVariance + measurementVariance);

  return {
    latitude: state.latitude + gain * (fix.latitude - state.latitude),
    longitude: state.longitude + gain * (fix.longitude - state.longitude),
    variance: (1 - gain) * predictedVariance,
    timestampMs: fix.timestampMs,
  };
}

/** A minimum apparent-movement floor below which GPS receiver noise alone
 * fully explains the distance, regardless of how little time elapsed —
 * mirrors markerPlayback.ts's own MIN_SEGMENT_MOVE_M reasoning for the same
 * class of sub-jitter distances, scaled up slightly since this guards a
 * speed *ratio* rather than a rendered segment. */
const MIN_JUMP_CHECK_DISTANCE_M = 20;

/** Ceiling on physically plausible ground speed for a licensed vehicle on a
 * public road (216 km/h) — matches markerPlayback.ts's own
 * MAX_PLAUSIBLE_SPEED_MPS so both stages of the pipeline agree on what
 * "impossible" means. */
export const MAX_PLAUSIBLE_SPEED_MPS = 60;

/**
 * True when a new fix implies a speed no real vehicle could have covered
 * since the last accepted fix — the physics-based check behind Uber's
 * Beacon system's GPS-jump rejection (accelerometer-consistency, without
 * this app building full sensor fusion): a jump requiring an impossible
 * instantaneous speed is GPS noise/multipath, not a real position, and
 * should be dropped outright rather than accepted or used to reset the
 * buffer. Unlike a pure distance threshold (e.g. CarMarker's own
 * SNAP_DISTANCE_M), this naturally tells a genuine gap apart from a glitch:
 * the same 500 m difference is entirely plausible after a 30 s tunnel/
 * backgrounding gap (60 km/h) and physically impossible within 1 s (1800
 * km/h) — the elapsed time is what distinguishes them, not the distance
 * alone.
 */
export function isImplausibleJump(
  last: { latitude: number; longitude: number; timestampMs: number } | null,
  fix: { latitude: number; longitude: number; timestampMs: number },
  maxPlausibleSpeedMps: number = MAX_PLAUSIBLE_SPEED_MPS,
): boolean {
  if (!last) return false;
  const elapsedMs = fix.timestampMs - last.timestampMs;
  if (elapsedMs <= 0) return false; // duplicate/out-of-order — not this function's concern
  const distanceM = distanceMeters(last.latitude, last.longitude, fix.latitude, fix.longitude);
  if (distanceM < MIN_JUMP_CHECK_DISTANCE_M) return false;
  const impliedSpeedMps = distanceM / (elapsedMs / 1000);
  return impliedSpeedMps > maxPlausibleSpeedMps;
}
