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

  // An out-and-back route: outbound leg (segments 0-1, eastbound) and a
  // return leg (segments 3-4, westbound) running ~1.1m apart in parallel —
  // the exact geometry (a divided road, a narrow loop) that let a pure
  // nearest-distance search flip the bearing 90-180° for one tick.
  const OUT_AND_BACK = [
    { latitude: 50.4383, longitude: -104.63 },    // 0 — outbound start
    { latitude: 50.4383, longitude: -104.625 },   // 1 — outbound mid
    { latitude: 50.4383, longitude: -104.62 },    // 2 — turn point
    { latitude: 50.43831, longitude: -104.62 },   // 3 — return start (~1.1m north)
    { latitude: 50.43831, longitude: -104.625 },  // 4 — return mid
    { latitude: 50.43831, longitude: -104.63 },   // 5 — return end
  ];
  // Slightly closer to the outbound leg (segment 0, ~0.33m away) than the
  // return leg (segment 4, ~0.78m away) — simulates GPS jitter nudging a
  // car that's actually on the return leg toward the nearby outbound one.
  const AMBIGUOUS_FIX = { latitude: 50.4383 + 0.000003, longitude: -104.6275 };

  it('without a continuity hint, picks the globally-nearest segment even if it runs backward', () => {
    const snap = snapToRoute(AMBIGUOUS_FIX, OUT_AND_BACK);
    expect(snap!.segmentIndex).toBe(0);
    expect(snap!.bearing).toBeCloseTo(90, 0); // eastbound — wrong if the car is on the return leg
  });

  it('with a continuity hint, stays on the segment the car was actually snapped to', () => {
    // Car was last snapped to segment 4 (the return leg, westbound).
    const snap = snapToRoute(AMBIGUOUS_FIX, OUT_AND_BACK, 35, 4);
    expect(snap!.segmentIndex).toBe(4);
    expect(snap!.bearing).toBeCloseTo(270, 0); // westbound — correct
  });

  it('allows a small backward tolerance (1 segment) instead of only ever moving forward', () => {
    // A bend: segment 0 runs east from v0 to v1, segment 1 turns north from
    // v1 to v2. A fix just southwest of the bend is genuinely closer to
    // segment 0 (~1.1m) than segment 1 (~7.2m clamped to v1) — but both are
    // well within maxSnapMeters, so without ANY backward tolerance a
    // continuity hint of 1 would restrict the search to segment 1 alone and
    // happily accept that farther (wrong) match rather than falling back.
    const bendRoute = [
      { latitude: 50.4383, longitude: -104.63 },   // 0
      { latitude: 50.4383, longitude: -104.625 },  // 1 — bend
      { latitude: 50.439, longitude: -104.625 },   // 2
    ];
    const fix = { latitude: 50.43829, longitude: -104.6251 };
    const snap = snapToRoute(fix, bendRoute, 35, 1);
    expect(snap!.segmentIndex).toBe(0);
  });

  it('falls back to an unrestricted search when the continuity window has nothing close enough', () => {
    // A continuity hint entirely out of range (e.g. after a route swap) must
    // never permanently block re-snapping — it should just fall back to the
    // same global search as if no hint were given.
    const snap = snapToRoute(AMBIGUOUS_FIX, OUT_AND_BACK, 35, 100);
    expect(snap!.segmentIndex).toBe(0);
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

describe('destinationPoint', () => {
  const { destinationPoint } = require('@shared/utils/vehicleTracking');
  const origin = { latitude: 50.4383, longitude: -104.62 };

  it('east by 100m moves longitude only, by ~100m', () => {
    const d = destinationPoint(origin, 90, 100);
    expect(d.latitude).toBeCloseTo(origin.latitude, 5);
    expect(distanceMeters(origin.latitude, origin.longitude, d.latitude, d.longitude)).toBeCloseTo(100, 0);
    expect(d.longitude).toBeGreaterThan(origin.longitude);
  });

  it('north by 250m moves latitude only, by ~250m', () => {
    const d = destinationPoint(origin, 0, 250);
    expect(d.longitude).toBeCloseTo(origin.longitude, 6);
    expect(distanceMeters(origin.latitude, origin.longitude, d.latitude, d.longitude)).toBeCloseTo(250, 0);
    expect(d.latitude).toBeGreaterThan(origin.latitude);
  });

  it('round-trips: forward then back lands at the origin', () => {
    const out = destinationPoint(origin, 237, 400);
    const back = destinationPoint(out, (237 + 180) % 360, 400);
    expect(back.latitude).toBeCloseTo(origin.latitude, 6);
    expect(back.longitude).toBeCloseTo(origin.longitude, 6);
  });
});
