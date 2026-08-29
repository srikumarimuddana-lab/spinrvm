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
    const p = playbackPosition(buf, 20_000)!; // 2s past newest, cap 3s
    expect(p.mode).toBe('extrapolating');
    // Last segment covered 0.0004 deg in 4s → +0.0002 deg in 2s.
    expect(p.coordinate.longitude).toBeCloseTo(-104.63 + 0.001, 10);
    expect(p.bearing).toBeCloseTo(90, 0);
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
