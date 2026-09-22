/**
 * Tests for shared/utils/markerPlayback — the playback-buffer engine behind
 * continuous car-marker motion. Lives in rider-app/__tests__ because no CI
 * jest run collects shared/__tests__ (same as vehicleTracking.test.ts).
 */
import {
  MAX_EXTRAPOLATION_MS,
  PLAYBACK_DELAY_MS,
  playbackPosition,
  pushFix,
  shouldResetBuffer,
  type PlaybackFix,
} from '@shared/utils/markerPlayback';

// Eastbound along an east-west street: ~7.1 m per 1e-4 deg lng at lat 50.44.
const fix = (tSec: number, lngOffset: number): PlaybackFix => ({
  latitude: 50.4383,
  longitude: -104.63 + lngOffset,
  timestampMs: tSec * 1000,
});

describe('pushFix', () => {
  it('appends in order and rejects out-of-order/duplicate timestamps', () => {
    const buf: PlaybackFix[] = [];
    pushFix(buf, fix(10, 0), 10_000);
    pushFix(buf, fix(12, 0.0001), 12_000);
    pushFix(buf, fix(11, 0.00005), 12_000); // out of order — dropped
    pushFix(buf, fix(12, 0.0002), 12_000); // duplicate ts — dropped
    expect(buf.map((f) => f.timestampMs)).toEqual([10_000, 12_000]);
  });

  it('prunes fixes no longer reachable by the render time, keeping a left endpoint', () => {
    const buf: PlaybackFix[] = [];
    for (let s = 0; s <= 30; s += 2) pushFix(buf, fix(s, s * 0.0001), s * 1000);
    // now=30s, delay 5s → render time 25s; everything with a successor ≤ 25s prunes.
    const tss = buf.map((f) => f.timestampMs / 1000);
    expect(tss[0]).toBeLessThanOrEqual(25); // left endpoint kept
    expect(tss[1]).toBeGreaterThan(25 - PLAYBACK_DELAY_MS / 1000);
    expect(tss[tss.length - 1]).toBe(30);
    expect(buf.length).toBeLessThan(8);
  });
});

describe('playbackPosition', () => {
  const buf: PlaybackFix[] = [fix(10, 0), fix(14, 0.0004), fix(18, 0.0008)];

  it('returns null on an empty buffer', () => {
    expect(playbackPosition([], 0)).toBeNull();
  });

  it('holds at the first fix before playback has data', () => {
    const p = playbackPosition(buf, 9_000)!;
    expect(p.mode).toBe('waiting');
    expect(p.coordinate.longitude).toBeCloseTo(-104.63, 10);
  });

  it('interpolates linearly between bracketing fixes with an eastbound bearing', () => {
    const p = playbackPosition(buf, 12_000)!; // halfway 10s→14s
    expect(p.mode).toBe('interpolating');
    expect(p.coordinate.longitude).toBeCloseTo(-104.63 + 0.0002, 10);
    expect(p.coordinate.latitude).toBeCloseTo(50.4383, 10);
    expect(p.bearing).toBeCloseTo(90, 0);
  });

  it('interpolates inside the newest segment too', () => {
    const p = playbackPosition(buf, 17_000)!; // 3/4 through 14s→18s
    expect(p.mode).toBe('interpolating');
    expect(p.coordinate.longitude).toBeCloseTo(-104.63 + 0.0007, 10);
  });

  it('dead-reckons past the newest fix along the last segment velocity', () => {
    const p = playbackPosition(buf, 19_000)!; // 1s past newest, cap 1.5s
    expect(p.mode).toBe('extrapolating');
    // Last segment covered 0.0004 deg in 4s → +0.0001 deg in 1s.
    expect(p.coordinate.longitude).toBeCloseTo(-104.63 + 0.0009, 10);
    expect(p.bearing).toBeCloseTo(90, 0);
    expect(p.speedMps).toBeGreaterThan(0);
  });

  it('never extrapolates a near-stopped segment (the red-light drift regression)', () => {
    // The car was crawling at ~0.5 m/s (well under the 1.5 m/s gate) when
    // the buffer went dry — e.g. it had just come to a stop and Android's
    // distanceInterval-gated GPS stopped delivering fixes. Extrapolating
    // this forward is exactly what drove the marker through a red light it
    // was stopped at (live-testing report 2026-08-30).
    const nearlyStopped: PlaybackFix[] = [fix(10, 0), fix(14, 0.00003)]; // ~2.1m/4s
    const p = playbackPosition(nearlyStopped, 15_000)!;
    expect(p.mode).toBe('holding');
    expect(p.coordinate.longitude).toBeCloseTo(-104.63 + 0.00003, 10);
  });

  it('shrinks the extrapolation window when the last two segments show deceleration', () => {
    // seg 10s->12s: 0.001 deg (~71m) in 2s ≈ 35.5 m/s. seg 12s->14s: 0.0001
    // deg (~7.1m) in 2s ≈ 3.55 m/s — braking hard. Neither segment alone
    // trips MIN_EXTRAPOLATION_SPEED_MPS (both well above 1.5 m/s), so
    // without the deceleration-aware shrink this would extrapolate all the
    // way to the full 1.5s cap — reproducing "went on, then came back" for
    // a car braking to a stop from real speed (live-testing report
    // 2026-09-05), which the pre-existing near-stopped-segment guard above
    // does not cover (that guard only catches a car ALREADY slow/idling
    // before the gap began).
    const decelerating: PlaybackFix[] = [fix(10, 0), fix(12, 0.001), fix(14, 0.0011)];
    // 300ms overshoot: comfortably under the full 1.5s cap, but should be
    // shrunk well past by the ~10x speed drop (ratio ≈ 3.55/35.5 ≈ 0.1 →
    // effective cap ≈ 150ms).
    const p = playbackPosition(decelerating, 14_300)!;
    expect(p.mode).toBe('holding');
    expect(p.coordinate.longitude).toBeCloseTo(-104.63 + 0.0011, 10);
  });

  it('does not shrink (or lengthen) the window when speed is steady or increasing', () => {
    // seg 10s->12s slower, seg 12s->14s faster — accelerating, not
    // decelerating. The window must stay at the full cap.
    const accelerating: PlaybackFix[] = [fix(10, 0), fix(12, 0.0001), fix(14, 0.0011)];
    const p = playbackPosition(accelerating, 14_300)!;
    expect(p.mode).toBe('extrapolating');
  });

  it('uses the full (unshrunk) window when there is no earlier segment to compare against', () => {
    // Only 2 fixes buffered — nothing to detect a deceleration trend from.
    // Must behave exactly as before this fix: full cap, normal extrapolation.
    const twoFixes: PlaybackFix[] = [fix(10, 0), fix(14, 0.0004)];
    const p = playbackPosition(twoFixes, 15_000)!;
    expect(p.mode).toBe('extrapolating');
  });

  it('reports the bracketing segment ground speed while interpolating', () => {
    const p = playbackPosition(buf, 12_000)!;
    // 0.0004 deg lng ≈ 28.4 m at lat 50.44, over 4 s ≈ 7.1 m/s.
    expect(p.speedMps).toBeCloseTo(7.1, 0);
  });

  it('samples a smooth curve through a turn instead of the straight chord', () => {
    // Eastbound then northbound: an L-turn at the middle fix. The spline
    // should round the corner — at the midpoint of the second segment the
    // position must deviate from the straight chord toward the outside.
    const turn: PlaybackFix[] = [
      { latitude: 50.4383, longitude: -104.6310, timestampMs: 10_000 },
      { latitude: 50.4383, longitude: -104.6306, timestampMs: 14_000 },
      { latitude: 50.4383, longitude: -104.6302, timestampMs: 18_000 }, // corner
      { latitude: 50.4386, longitude: -104.6302, timestampMs: 22_000 }, // north
      { latitude: 50.4390, longitude: -104.6302, timestampMs: 26_000 },
    ];
    const atCorner = playbackPosition(turn, 18_000)!;
    // Passes exactly through the buffered fix (interpolation, not smoothing-away).
    expect(atCorner.coordinate.latitude).toBeCloseTo(50.4383, 8);
    expect(atCorner.coordinate.longitude).toBeCloseTo(-104.6302, 8);
    // AT the corner vertex the spline tangent is already mid-rotation —
    // between east (90) and north (0) — where a linear chord would still
    // report the full 90 of one leg and then snap. (Verified 40.3°.)
    expect(atCorner.bearing).not.toBeNull();
    expect(atCorner.bearing! > 0 && atCorner.bearing! < 90).toBe(true);

    // A second into the northbound leg the heading has settled near north
    // (small self-correcting Catmull-Rom overshoot past 0 is acceptable).
    const afterTurn = playbackPosition(turn, 19_000)!;
    expect(afterTurn.mode).toBe('interpolating');
    const b = afterTurn.bearing!;
    const northDelta = Math.min(b, 360 - b);
    expect(northDelta).toBeLessThan(15);
  });

  it('holds at the newest fix once the extrapolation cap is exceeded', () => {
    const p = playbackPosition(buf, 18_000 + MAX_EXTRAPOLATION_MS + 1)!;
    expect(p.mode).toBe('holding');
    expect(p.coordinate.longitude).toBeCloseTo(-104.63 + 0.0008, 10);
    expect(p.bearing).toBeNull();
  });

  it('holds instead of extrapolating from a stationary tail (no jitter drift)', () => {
    const still: PlaybackFix[] = [fix(10, 0.0008), { ...fix(14, 0.0008) }];
    const p = playbackPosition(still, 16_000)!;
    expect(p.mode).toBe('holding');
  });

  it('holds instead of extrapolating an implausible (teleport) segment', () => {
    const glitch: PlaybackFix[] = [fix(10, 0), fix(11, 0.05)]; // ~3.5 km in 1s
    const p = playbackPosition(glitch, 12_000)!;
    expect(p.mode).toBe('holding');
  });

  it('single-fix buffer: waits before, holds after', () => {
    const one = [fix(10, 0)];
    expect(playbackPosition(one, 9_000)!.mode).toBe('waiting');
    expect(playbackPosition(one, 12_000)!.mode).toBe('holding');
  });
});

describe('shouldResetBuffer', () => {
  it('flags a fix beyond the snap distance and accepts a nearby one', () => {
    const buf = [fix(10, 0)];
    expect(shouldResetBuffer(buf, { latitude: 50.4383, longitude: -104.62 }, 500)).toBe(true); // ~710m
    expect(shouldResetBuffer(buf, { latitude: 50.4383, longitude: -104.6295 }, 500)).toBe(false);
    expect(shouldResetBuffer([], { latitude: 0, longitude: 0 }, 500)).toBe(false);
  });
});
