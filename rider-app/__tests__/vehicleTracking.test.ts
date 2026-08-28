/**
 * Tests for shared/utils/vehicleTracking — the route-snapping and rotation
 * math behind the smooth car marker (rider + driver maps).
 *
 * Lives in rider-app/__tests__ (not shared/utils/__tests__) because no CI
 * jest run collects tests from the shared/ package — rider-app's suite is
 * where these actually execute.
 */
import {
  bearingDegrees,
  distanceMeters,
  shortestArcRotationTarget,
  snapToRoute,
} from '@shared/utils/vehicleTracking';

// Regina Ave, Regina SK runs east–west near lng -104.6; ~1e-4 deg lat ≈ 11 m.
const REGINA_AVE = [
  { latitude: 50.4383, longitude: -104.63 },
  { latitude: 50.4383, longitude: -104.62 },
  { latitude: 50.4383, longitude: -104.61 },
];

describe('distanceMeters / bearingDegrees', () => {
  it('measures ~111m per 0.001 deg of latitude', () => {
    const d = distanceMeters(50.0, -104.6, 50.001, -104.6);
    expect(d).toBeGreaterThan(105);
    expect(d).toBeLessThan(118);
  });

  it('bearing due east is 90, due north is 0', () => {
    expect(bearingDegrees(50.4383, -104.63, 50.4383, -104.62)).toBeCloseTo(90, 0);
    expect(bearingDegrees(50.4383, -104.63, 50.4483, -104.63)).toBeCloseTo(0, 0);
  });
});

describe('snapToRoute', () => {
  it('snaps a fix ~10m off an east-west street onto the street with bearing 90', () => {
    const fix = { latitude: 50.4384, longitude: -104.625 }; // ~11m north of the line
    const snap = snapToRoute(fix, REGINA_AVE);
    expect(snap).not.toBeNull();
    expect(snap!.coordinate.latitude).toBeCloseTo(50.4383, 4);
    expect(snap!.coordinate.longitude).toBeCloseTo(-104.625, 4);
    expect(snap!.bearing).toBeCloseTo(90, 0);
    expect(snap!.deviationMeters).toBeGreaterThan(5);
    expect(snap!.deviationMeters).toBeLessThan(20);
  });

  it('returns null when the fix is farther than maxSnapMeters (off-route)', () => {
    const farFix = { latitude: 50.4393, longitude: -104.625 }; // ~110m north
    expect(snapToRoute(farFix, REGINA_AVE)).toBeNull();
    // But a widened tolerance accepts it.
    expect(snapToRoute(farFix, REGINA_AVE, 200)).not.toBeNull();
  });

  it('returns null for a missing or degenerate route', () => {
    const fix = { latitude: 50.4383, longitude: -104.625 };
    expect(snapToRoute(fix, null)).toBeNull();
    expect(snapToRoute(fix, [])).toBeNull();
    expect(snapToRoute(fix, [REGINA_AVE[0]])).toBeNull();
  });

  it('clamps to segment endpoints past the end of the route', () => {
    const pastEnd = { latitude: 50.4383, longitude: -104.605 }; // east of last vertex
    const snap = snapToRoute(pastEnd, REGINA_AVE, 500);
    expect(snap!.coordinate.longitude).toBeCloseTo(-104.61, 4);
    expect(snap!.segmentIndex).toBe(1);
  });

  it('skips non-finite vertices instead of producing NaN', () => {
    const route = [
      { latitude: NaN, longitude: -104.63 },
      ...REGINA_AVE,
    ];
    const fix = { latitude: 50.4384, longitude: -104.625 };
    const snap = snapToRoute(fix, route);
    expect(snap).not.toBeNull();
    expect(Number.isFinite(snap!.coordinate.latitude)).toBe(true);
  });
});

describe('shortestArcRotationTarget', () => {
  it('crosses 0/360 the short way (350 -> 10 becomes 370)', () => {
    expect(shortestArcRotationTarget(350, 10)).toBe(370);
  });

  it('turns back the short way (10 -> 350 becomes -10)', () => {
    expect(shortestArcRotationTarget(10, 350)).toBe(-10);
  });

  it('handles accumulated rotations beyond 360', () => {
    // Current 730 (= 10 normalized), target 20 -> 740.
    expect(shortestArcRotationTarget(730, 20)).toBe(740);
  });

  it('no-op when already at the target bearing', () => {
    expect(shortestArcRotationTarget(90, 90)).toBe(90);
  });

  it('handles negative accumulated rotation', () => {
    // -350 normalizes to 10; target 350 -> shortest arc is -20 -> -370.
    expect(shortestArcRotationTarget(-350, 350)).toBe(-370);
  });
});
