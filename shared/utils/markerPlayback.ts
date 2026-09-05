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
  /** Ground speed of the active segment in m/s, or null when held/waiting. */
  speedMps: number | null;
}

/** How far in the past the marker renders. Large enough that at the fastest
 * trip cadence (2 s) two fixes are always buffered ahead of the render time;
 * small enough that riders/drivers never perceive the lag (Uber/Lyft ship
 * comparable delays). */
export const PLAYBACK_DELAY_MS = 5_000;
/** Dead-reckoning cap — beyond this, predicting invents too much road.
 * Paired with the fix producer's stationary heartbeat (re-emits the last
 * coordinate every ~2.5 s when no real fix arrives), so a genuine network
 * gap is still covered while a driver who has actually stopped is corrected
 * well within one cap window instead of drifting for a full 3 s. */
export const MAX_EXTRAPOLATION_MS = 1_500;
/** Ignore sub-jitter segments when deriving bearing or velocity. */
const MIN_SEGMENT_MOVE_M = 2;
/** A segment implying faster than this is a glitch, not driving (216 km/h). */
const MAX_PLAUSIBLE_SPEED_MPS = 60;
/**
 * Below this speed, the last segment is "stopped or crawling" — extrapolating
 * it forward invents motion a driver never made. This was the root cause of
 * the car visibly driving through a red light it was stopped at, then
 * snapping backward once a real (stationary) fix arrived: Android's
 * distanceInterval-gated GPS goes quiet at a standstill, the buffer ran dry,
 * and the last pre-stop segment's real velocity got projected forward for up
 * to 3 s (live-testing report 2026-08-30, screenshots of the marker
 * reversing at a signal). A car that was already slow/stopped when the gap
 * began must hold, not coast.
 */
const MIN_EXTRAPOLATION_SPEED_MPS = 1.5;

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
 * Catmull-Rom (cubic Hermite) sample through the buffered fixes for the
 * bracketing segment [p1→p2] at fraction `t`, with one-sided clamping at the
 * buffer edges. Linear chords between 2–4 s fixes cut corners and kink the
 * heading at every vertex; the spline is C¹-continuous, so both the position
 * AND the tangent (bearing) flow smoothly through each fix — the JS-only
 * approximation of Uber's predicted-points-along-path animation.
 *
 * Longitude is scaled by cos(lat) inside the tangent math implicitly via the
 * small-angle degree space — at the ≤100 m spans between fixes the error is
 * far below GPS noise. Falls back to `null` (caller uses linear) when a
 * neighbor segment is implausible (teleport glitch) so a bad fix cannot bend
 * the curve toward it.
 */
function splineSample(
  p0: PlaybackFix,
  p1: PlaybackFix,
  p2: PlaybackFix,
  p3: PlaybackFix,
  t: number,
): { coordinate: TrackingLatLng; bearing: number | null } | null {
  const span = p2.timestampMs - p1.timestampMs;
  if (span <= 0) return null;
  for (const [a, b] of [[p0, p1], [p2, p3]] as const) {
    const ms = b.timestampMs - a.timestampMs;
    if (ms < 0) return null;
    if (ms > 0) {
      const speed = (distanceMeters(a.latitude, a.longitude, b.latitude, b.longitude) / ms) * 1000;
      if (speed > MAX_PLAUSIBLE_SPEED_MPS) return null;
    }
  }
  // Non-uniform Catmull-Rom tangents on the time parameterization, scaled to
  // this segment's span (finite differences over the neighbor windows).
  const t0 = p1.timestampMs - p0.timestampMs;
  const t2 = p3.timestampMs - p2.timestampMs;
  const m1lat = ((p2.latitude - p0.latitude) / (t0 + span || span)) * span;
  const m1lng = ((p2.longitude - p0.longitude) / (t0 + span || span)) * span;
  const m2lat = ((p3.latitude - p1.latitude) / (span + t2 || span)) * span;
  const m2lng = ((p3.longitude - p1.longitude) / (span + t2 || span)) * span;

  const tt = t * t;
  const ttt = tt * t;
  const h00 = 2 * ttt - 3 * tt + 1;
  const h10 = ttt - 2 * tt + t;
  const h01 = -2 * ttt + 3 * tt;
  const h11 = ttt - tt;
  const coordinate = {
    latitude: h00 * p1.latitude + h10 * m1lat + h01 * p2.latitude + h11 * m2lat,
    longitude: h00 * p1.longitude + h10 * m1lng + h01 * p2.longitude + h11 * m2lng,
  };
  // Tangent (derivative of the Hermite basis) → instantaneous bearing.
  const d00 = 6 * tt - 6 * t;
  const d10 = 3 * tt - 4 * t + 1;
  const d01 = -6 * tt + 6 * t;
  const d11 = 3 * tt - 2 * t;
  const dLat = d00 * p1.latitude + d10 * m1lat + d01 * p2.latitude + d11 * m2lat;
  const dLng = d00 * p1.longitude + d10 * m1lng + d01 * p2.longitude + d11 * m2lng;
  const dx = dLng * Math.cos((coordinate.latitude * Math.PI) / 180);
  const magnitude = Math.hypot(dx, dLat);
  const bearing =
    magnitude > 1e-9 ? ((Math.atan2(dx, dLat) * 180) / Math.PI + 360) % 360 : null;
  return { coordinate, bearing };
}

/**
 * Where the marker should be at `renderTimeMs` (typically now − delay).
 * Returns null only for an empty buffer.
 *
 * With 4+ buffered fixes around the render time, the position and bearing are
 * sampled from a Catmull-Rom spline through the fixes (smooth corners, smooth
 * heading); otherwise linear interpolation between the bracketing pair.
 * `speedMps` is always the bracketing segment's average ground speed, so the
 * caller can reason about motion (bearing gating, camera zoom) without
 * re-deriving it.
 */
export function playbackPosition(
  buffer: readonly PlaybackFix[],
  renderTimeMs: number,
  maxExtrapolationMs: number = MAX_EXTRAPOLATION_MS,
): PlaybackPosition | null {
  if (buffer.length === 0) return null;

  const first = buffer[0];
  if (renderTimeMs <= first.timestampMs) {
    return { coordinate: first, bearing: null, mode: 'waiting', speedMps: null };
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
        const segM = distanceMeters(a.latitude, a.longitude, b.latitude, b.longitude);
        const speedMps = span > 0 ? (segM / span) * 1000 : 0;

        // Spline path: needs the fix on either side; edges clamp one-sided.
        const smooth =
          segM >= MIN_SEGMENT_MOVE_M
            ? splineSample(buffer[i - 1] ?? a, a, b, buffer[i + 2] ?? b, t)
            : null;
        if (smooth) {
          return { ...smooth, mode: 'interpolating', speedMps };
        }
        const coordinate = {
          latitude: a.latitude + (b.latitude - a.latitude) * t,
          longitude: a.longitude + (b.longitude - a.longitude) * t,
        };
        const bearing =
          segM >= MIN_SEGMENT_MOVE_M
            ? bearingDegrees(a.latitude, a.longitude, b.latitude, b.longitude)
            : null;
        return { coordinate, bearing, mode: 'interpolating', speedMps };
      }
    }
    // Unreachable (renderTime > first guaranteed above), but stay safe.
    return { coordinate: first, bearing: null, mode: 'waiting', speedMps: null };
  }

  // Past the newest fix: dead-reckon along the last segment's velocity, but
  // shrink how far into the future that's trusted when the two most recent
  // segments show the car slowing down. MIN_EXTRAPOLATION_SPEED_MPS above
  // only catches a car that was ALREADY slow/idling before the gap began —
  // it does nothing for the far more common case of braking to a stop FROM
  // real driving speed: the last buffered segment still reads well above
  // that threshold, so the full cap gets used and the marker visibly drives
  // on through the stop before snapping back once a real (stationary) fix
  // lands or the cap expires ("went on, then came back" — live-testing
  // report 2026-09-05). There's no reported instantaneous speed in this
  // pipeline to know a car has JUST stopped, but a falling speed trend
  // across the last two segments is a cheap, no-new-data signal that a stop
  // may be close — so the extrapolation window is scaled down proportionally
  // to that trend (never lengthened, only ever shortened or left as-is).
  const overshootMs = renderTimeMs - newest.timestampMs;
  const prev = buffer.length >= 2 ? buffer[buffer.length - 2] : null;
  const priorToPrev = buffer.length >= 3 ? buffer[buffer.length - 3] : null;
  let effectiveMaxExtrapolationMs = maxExtrapolationMs;
  if (prev && priorToPrev) {
    const priorSegMs = prev.timestampMs - priorToPrev.timestampMs;
    const priorSegM = distanceMeters(
      priorToPrev.latitude, priorToPrev.longitude, prev.latitude, prev.longitude,
    );
    const priorSpeed = priorSegMs > 0 ? (priorSegM / priorSegMs) * 1000 : 0;
    const lastSegMs = newest.timestampMs - prev.timestampMs;
    const lastSegM = distanceMeters(prev.latitude, prev.longitude, newest.latitude, newest.longitude);
    const lastSpeed = lastSegMs > 0 ? (lastSegM / lastSegMs) * 1000 : 0;
    if (priorSpeed > 0) {
      const decelRatio = Math.min(1, lastSpeed / priorSpeed);
      effectiveMaxExtrapolationMs = maxExtrapolationMs * decelRatio;
    }
  }
  if (prev && overshootMs <= effectiveMaxExtrapolationMs) {
    const segMs = newest.timestampMs - prev.timestampMs;
    const segM = distanceMeters(prev.latitude, prev.longitude, newest.latitude, newest.longitude);
    const speed = segMs > 0 ? (segM / segMs) * 1000 : 0;
    if (
      segM >= MIN_SEGMENT_MOVE_M &&
      speed <= MAX_PLAUSIBLE_SPEED_MPS &&
      speed >= MIN_EXTRAPOLATION_SPEED_MPS
    ) {
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
        speedMps: speed,
      };
    }
  }
  return { coordinate: newest, bearing: null, mode: 'holding', speedMps: null };
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
