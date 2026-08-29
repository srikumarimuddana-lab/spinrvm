/**
 * Playback-buffer positioning for the live car marker (the Lyft technique).
 *
 * Instead of animating to each GPS fix as it arrives — which leaves the car
 * parked between fixes and then leaping ("stays at one place and then jumps",
 * live-testing report 2026-08-31) — the marker renders the car a fixed few
 * seconds in the PAST and animates continuously through a queue of buffered
 * fixes. Because the next point is already known, motion never stalls waiting
 * for the network. When the buffer runs dry (network gap, tunnel) the position
 * extrapolates along the last known velocity for a short capped window, then
 * holds honestly rather than inventing distance.
 *
 * Pure math, no timers or React — the consumer ticks it with a render time.
 */

import { bearingDegrees, distanceMeters, type TrackingLatLng } from './vehicleTracking';

export interface PlaybackFix extends TrackingLatLng {
  /** Wall-clock ms when the fix was received on this device. */
  timestampMs: number;
}

export type PlaybackMode =
  /** Render time is before the first buffered fix — hold at the first fix. */
  | 'waiting'
  /** Render time lies between two buffered fixes — true interpolation. */
  | 'interpolating'
  /** Past the newest fix but within the extrapolation cap — dead reckoning. */
  | 'extrapolating'
  /** Past the cap with no new data — hold at the newest fix. */
  | 'holding';

export interface PlaybackPosition {
  coordinate: TrackingLatLng;
  /** Direction of motion for this instant, or null when not moving/known. */
  bearing: number | null;
  mode: PlaybackMode;
}

/** How far in the past the marker renders. Large enough that at the fastest
 * trip cadence (2 s) two fixes are always buffered ahead of the render time;
 * small enough that riders/drivers never perceive the lag (Uber/Lyft ship
 * comparable delays). */
export const PLAYBACK_DELAY_MS = 5_000;
/** Dead-reckoning cap — beyond this, predicting invents too much road. */
export const MAX_EXTRAPOLATION_MS = 3_000;
/** Ignore sub-jitter segments when deriving bearing or velocity. */
const MIN_SEGMENT_MOVE_M = 2;
/** A segment implying faster than this is a glitch, not driving (216 km/h). */
const MAX_PLAUSIBLE_SPEED_MPS = 60;

/**
 * Append a fix and prune entries no longer reachable by the render time.
 * Mutates and returns `buffer` (callers hold it in a ref). Keeps one fix
 * older than the oldest possible render time so interpolation always has a
 * left endpoint.
 */
export function pushFix(
  buffer: PlaybackFix[],
  fix: PlaybackFix,
  nowMs: number,
  delayMs: number = PLAYBACK_DELAY_MS,
): PlaybackFix[] {
  // Out-of-order or duplicate timestamps would corrupt interpolation.
  const last = buffer[buffer.length - 1];
  if (!last || fix.timestampMs > last.timestampMs) {
    buffer.push(fix);
  }
  const oldestNeeded = nowMs - delayMs;
  while (buffer.length > 2 && buffer[1].timestampMs <= oldestNeeded) {
    buffer.shift();
  }
  return buffer;
}

/**
 * Where the marker should be at `renderTimeMs` (typically now − delay).
 * Returns null only for an empty buffer.
 */
export function playbackPosition(
  buffer: readonly PlaybackFix[],
  renderTimeMs: number,
  maxExtrapolationMs: number = MAX_EXTRAPOLATION_MS,
): PlaybackPosition | null {
  if (buffer.length === 0) return null;

  const first = buffer[0];
  if (renderTimeMs <= first.timestampMs) {
    return { coordinate: first, bearing: null, mode: 'waiting' };
  }

  const newest = buffer[buffer.length - 1];
  if (renderTimeMs <= newest.timestampMs) {
    // Find the bracketing pair. Buffers are tiny (≤ ~15 fixes) — linear scan.
    for (let i = buffer.length - 2; i >= 0; i--) {
      const a = buffer[i];
      if (a.timestampMs <= renderTimeMs) {
        const b = buffer[i + 1];
        const span = b.timestampMs - a.timestampMs;
        const t = span > 0 ? (renderTimeMs - a.timestampMs) / span : 1;
        const coordinate = {
          latitude: a.latitude + (b.latitude - a.latitude) * t,
          longitude: a.longitude + (b.longitude - a.longitude) * t,
        };
        const segM = distanceMeters(a.latitude, a.longitude, b.latitude, b.longitude);
        const bearing =
          segM >= MIN_SEGMENT_MOVE_M
            ? bearingDegrees(a.latitude, a.longitude, b.latitude, b.longitude)
            : null;
        return { coordinate, bearing, mode: 'interpolating' };
      }
    }
    // Unreachable (renderTime > first guaranteed above), but stay safe.
    return { coordinate: first, bearing: null, mode: 'waiting' };
  }

  // Past the newest fix: dead-reckon along the last segment's velocity.
  const overshootMs = renderTimeMs - newest.timestampMs;
  const prev = buffer.length >= 2 ? buffer[buffer.length - 2] : null;
  if (prev && overshootMs <= maxExtrapolationMs) {
    const segMs = newest.timestampMs - prev.timestampMs;
    const segM = distanceMeters(prev.latitude, prev.longitude, newest.latitude, newest.longitude);
    const speed = segMs > 0 ? (segM / segMs) * 1000 : 0;
    if (segM >= MIN_SEGMENT_MOVE_M && speed <= MAX_PLAUSIBLE_SPEED_MPS) {
      // Continue the segment's own direction proportionally.
      const t = overshootMs / segMs;
      const coordinate = {
        latitude: newest.latitude + (newest.latitude - prev.latitude) * t,
        longitude: newest.longitude + (newest.longitude - prev.longitude) * t,
      };
      return {
        coordinate,
        bearing: bearingDegrees(prev.latitude, prev.longitude, newest.latitude, newest.longitude),
        mode: 'extrapolating',
      };
    }
  }
  return { coordinate: newest, bearing: null, mode: 'holding' };
}

/**
 * True when a new raw fix is so far from the buffered tail that gliding to it
 * would lie (stale fix after backgrounding, ride handoff). The caller should
 * then reset the buffer to just this fix so the marker snaps once.
 */
export function shouldResetBuffer(
  buffer: readonly PlaybackFix[],
  fix: TrackingLatLng,
  snapDistanceM: number,
): boolean {
  const last = buffer[buffer.length - 1];
  if (!last) return false;
  return distanceMeters(last.latitude, last.longitude, fix.latitude, fix.longitude) > snapDistanceM;
}
