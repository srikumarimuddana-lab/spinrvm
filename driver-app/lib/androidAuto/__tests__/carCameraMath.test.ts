/**
 * Unit tests for lib/androidAuto/carCameraMath.ts — the arithmetic that decides
 * where the car map's camera points. No head unit, no native module.
 */
import {
  angularDelta,
  HEADING_COMMIT_DEG,
  normalizeHeading,
  shouldCommitHeading,
  zoomForSpan,
} from '../carCameraMath';

describe('normalizeHeading', () => {
  it('rejects the sentinels expo-location uses for "no course"', () => {
    // This is the whole reason the car marker pointed north while driving west:
    // a one-shot getCurrentPositionAsync has no course over ground.
    expect(normalizeHeading(-1)).toBeNull();
    expect(normalizeHeading(null)).toBeNull();
    expect(normalizeHeading(undefined)).toBeNull();
    expect(normalizeHeading(NaN)).toBeNull();
  });

  it('keeps a real bearing, wrapping into [0, 360)', () => {
    expect(normalizeHeading(0)).toBe(0);
    expect(normalizeHeading(271.4)).toBe(271.4);
    expect(normalizeHeading(360)).toBe(0);
    expect(normalizeHeading(370)).toBe(10);
  });
});

describe('angularDelta', () => {
  it('takes the short way round the compass', () => {
    // The regression this guards: crossing north spun the camera 340° the wrong
    // way instead of 20° the right way.
    expect(angularDelta(350, 10)).toBe(20);
    expect(angularDelta(10, 350)).toBe(-20);
  });

  it('is signed and bounded to (-180, 180]', () => {
    expect(angularDelta(0, 90)).toBe(90);
    expect(angularDelta(90, 0)).toBe(-90);
    expect(angularDelta(0, 180)).toBe(180);
    expect(angularDelta(0, 181)).toBe(-179);
    expect(angularDelta(45, 45)).toBe(0);
  });
});

describe('shouldCommitHeading', () => {
  it('never rotates on an absent bearing', () => {
    expect(shouldCommitHeading(90, null)).toBe(false);
    expect(shouldCommitHeading(null, null)).toBe(false);
  });

  it('adopts the first real bearing immediately', () => {
    expect(shouldCommitHeading(null, 271)).toBe(true);
  });

  it('ignores jitter but follows a real turn', () => {
    expect(shouldCommitHeading(90, 92)).toBe(false); // 2° — noise
    expect(shouldCommitHeading(90, 90 + HEADING_COMMIT_DEG)).toBe(true);
    expect(shouldCommitHeading(90, 180)).toBe(true);
  });

  it('measures the turn the short way, so north-crossing jitter stays jitter', () => {
    expect(shouldCommitHeading(359, 1)).toBe(false); // 2° across north
    expect(shouldCommitHeading(359, 10)).toBe(true); // 11° is a real turn
  });
});

describe('zoomForSpan', () => {
  const W = 1000;
  const H = 385; // the surface's ~2.6:1 proportion

  it('zooms in one level per halving of the span', () => {
    const a = zoomForSpan(0.02, W, H, 52)!;
    const b = zoomForSpan(0.01, W, H, 52)!;
    expect(b - a).toBeCloseTo(1, 6);
  });

  it('picks the binding axis, matching what newLatLngBounds framed', () => {
    // At a 2.6:1 aspect and Saskatchewan's latitude, Mercator makes LATITUDE the
    // tighter constraint — so the zoom must come from height, not width.
    const byLat = Math.log2((360 * H * Math.cos((52 * Math.PI) / 180)) / (256 * 0.02));
    expect(zoomForSpan(0.02, W, H, 52)).toBeCloseTo(byLat, 6);
  });

  it('returns a sane street-level zoom for the default span', () => {
    // DEFAULT_DELTA (0.02) should land around neighbourhood/street framing.
    const z = zoomForSpan(0.02, W, H, 52)!;
    expect(z).toBeGreaterThan(13);
    expect(z).toBeLessThan(17);
  });

  it('is null before the surface has been laid out', () => {
    expect(zoomForSpan(0.02, 0, 0, 52)).toBeNull();
    expect(zoomForSpan(0.02, NaN, H, 52)).toBeNull();
    expect(zoomForSpan(0.02, W, -5, 52)).toBeNull();
  });

  it('is null for a nonsense span rather than returning Infinity', () => {
    expect(zoomForSpan(0, W, H, 52)).toBeNull();
    expect(zoomForSpan(-1, W, H, 52)).toBeNull();
    expect(zoomForSpan(NaN, W, H, 52)).toBeNull();
  });

  it('stays inside Google’s zoom range at both extremes', () => {
    expect(zoomForSpan(0.000001, W, H, 52)!).toBeLessThanOrEqual(20);
    expect(zoomForSpan(359, W, H, 52)!).toBeGreaterThanOrEqual(1);
  });

  it('survives a polar latitude without dividing by zero', () => {
    const z = zoomForSpan(0.02, W, H, 90);
    expect(z).not.toBeNull();
    expect(Number.isFinite(z!)).toBe(true);
  });
});
