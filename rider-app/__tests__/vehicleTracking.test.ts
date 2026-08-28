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
  selectBearing,
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

describe('selectBearing — bearing source priority', () => {
  // Westbound along Regina Ave: same latitude, decreasing longitude.
  const FROM = { latitude: 50.4383, longitude: -104.62 };
  const WEST_OF_IT = { latitude: 50.4383, longitude: -104.6203 }; // ~21 m west
  const NORTH_OF_IT = { latitude: 50.43848, longitude: -104.62 }; // ~20 m north
  const MIN_MOVE = 3;

  const base = {
    snap: null,
    from: FROM,
    to: WEST_OF_IT,
    heading: null as number | null,
    hasMovementBearing: false,
    minMoveMeters: MIN_MOVE,
  };

  it('REGRESSION: a reported heading of 0 does not beat westbound movement', () => {
    // Android's Location has a separate hasBearing() flag; getBearing()
    // returns 0.0 when it is false, so "no course" arrives as a literal 0.
    // While that outranked the travel fallback, the marker slid west across
    // the map still pointing due north (live testing, Regina Ave).
    const r = selectBearing({ ...base, movedMeters: 21, heading: 0 });
    expect(r.source).toBe('travel');
    expect(r.bearing).toBeCloseTo(270, 0);
  });

  it('does not over-correct: a genuine northbound course still reads ~0', () => {
    // Demoting the reported heading must not cost a real due-north bearing —
    // movement measures it independently.
    const r = selectBearing({ ...base, to: NORTH_OF_IT, movedMeters: 20, heading: 0 });
    expect(r.source).toBe('travel');
    expect(r.bearing).toBeCloseTo(0, 0);
  });

  it('a route segment still outranks everything', () => {
    const snap = {
      coordinate: WEST_OF_IT,
      bearing: 265,
      segmentIndex: 0,
      deviationMeters: 4,
    };
    const r = selectBearing({ ...base, snap, movedMeters: 21, heading: 90 });
    expect(r.source).toBe('route');
    expect(r.bearing).toBe(265);
  });

  it('uses the reported heading below the movement threshold, before any movement bearing', () => {
    // Cold start, car stationary: a reported heading is the only evidence.
    const r = selectBearing({ ...base, movedMeters: 1, heading: 137 });
    expect(r.source).toBe('heading');
    expect(r.bearing).toBe(137);
  });

  it('REGRESSION: a stationary reported 0 cannot spin an established bearing to north', () => {
    // Westbound driver stopped at a light. Movement has already established
    // 270; a placeholder 0 arriving now must be ignored, not applied.
    const r = selectBearing({
      ...base,
      movedMeters: 1,
      heading: 0,
      hasMovementBearing: true,
    });
    expect(r.source).toBe('none');
    expect(r.bearing).toBeNull();
  });

  it('treats -1 (iOS invalid course) and null as no heading', () => {
    expect(selectBearing({ ...base, movedMeters: 1, heading: -1 }).source).toBe('none');
    expect(selectBearing({ ...base, movedMeters: 1, heading: null }).source).toBe('none');
    expect(selectBearing({ ...base, movedMeters: 1, heading: NaN }).source).toBe('none');
  });

  it('ignores sub-threshold movement rather than deriving from jitter', () => {
    const r = selectBearing({ ...base, movedMeters: MIN_MOVE - 0.5, heading: null });
    expect(r.source).toBe('none');
  });
});
