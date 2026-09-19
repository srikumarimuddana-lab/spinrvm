/**
 * Tests for `snapToRoute`'s `preferredFromIndex` continuity hint.
 *
 * Regression coverage for the "car drives sideways/backward" class of bug
 * (2026-09-11 test ride, ported to shared/rider-app 2026-09-12 — see
 * docs/known-forks.md and ACTION_ITEMS.md C90/C101): with a pure
 * global-nearest search, a route that runs close to itself for a stretch
 * (a divided road, an out-and-back leg, a tight U-turn) can snap the marker
 * onto a spatially-closer but wrong-direction segment, giving a bearing
 * that's 90-180 off from the car's actual travel direction. This had no
 * test anywhere in the repo (verified 2026-09-19 while auditing
 * ACTION_ITEMS.md C90) even though it's the fix's entire reason to exist.
 */
import { snapToRoute, bearingDegrees, type TrackingLatLng } from '../vehicleTracking';

// Build a route that goes out along one line and comes straight back along a
// parallel line 6m away (a tight out-and-back / divided-road shape), all at
// ~50N (Saskatchewan-ish) so the local lng/lat meter scale isn't 1:1.
const BASE_LAT = 50.0;
const BASE_LNG = -104.0;
const METERS_PER_DEG_LAT = 111320;
const METERS_PER_DEG_LNG = 111320 * Math.cos((BASE_LAT * Math.PI) / 180);

function offset(xMeters: number, latMeters: number): TrackingLatLng {
  return {
    latitude: BASE_LAT + latMeters / METERS_PER_DEG_LAT,
    longitude: BASE_LNG + xMeters / METERS_PER_DEG_LNG,
  };
}

// Outbound leg (heading east, latMeters=0): indices 0-2, segments 0-1.
const outbound0 = offset(0, 0);
const outbound1 = offset(100, 0);
const outbound2 = offset(200, 0);
// Short connector "turn" at the far end: index 2-3, segment 2.
const turnEnd = offset(200, 10);
// Return leg (heading west, latMeters=10, 10m north of the outbound leg):
// indices 3-5, segments 3-4.
const return1 = offset(100, 10);
const return0 = offset(0, 10);

const route: TrackingLatLng[] = [
  outbound0, // index 0
  outbound1, // index 1
  outbound2, // index 2
  turnEnd, // index 3 (same point as outbound2's far end + 10m north)
  return1, // index 4
  return0, // index 5
];

describe('snapToRoute — preferredFromIndex continuity hint', () => {
  it('without a continuity hint, GPS noise on the return leg can snap onto the closer-but-wrong outbound segment', () => {
    // Car is actually on the return leg (segment 4, x~50m, 10m north), but a
    // noisy fix nudges it 6m toward the outbound line (only 4m from the
    // outbound line vs 6m from its true return-leg line) — the exact
    // "two segments both within range" ambiguity the fix doc describes.
    const noisyFix = offset(50, 4);

    const result = snapToRoute(noisyFix, route, 35);

    expect(result).not.toBeNull();
    // Global-nearest search picks the outbound segment (index 0 or 1) —
    // spatially closer, but pointing the wrong way (east, ~90°) while the
    // car is actually travelling west (~270°) on the return leg.
    expect(result!.segmentIndex).toBeLessThanOrEqual(1);
    expect(result!.bearing).toBeCloseTo(90, 0);
  });

  it('with preferredFromIndex set to the previous tick\'s segment, the same noisy fix stays on the correct return-leg segment', () => {
    const noisyFix = offset(50, 4);

    // Previous tick landed on segment 4 (return leg) — pass it forward as
    // the continuity hint, exactly as CarMarker.tsx does each tick.
    const result = snapToRoute(noisyFix, route, 35, 4);

    expect(result).not.toBeNull();
    expect(result!.segmentIndex).toBe(4);
    // Correct travel direction: west, not the outbound segment's east.
    expect(result!.bearing).toBeCloseTo(270, 0);
  });

  it('falls back to an unrestricted search when nothing near the continuity window is close enough (genuine reroute)', () => {
    // Continuity hint points far past the end of a short route — nothing
    // reachable within SEGMENT_BACKWARD_TOLERANCE of that index, so the
    // fix must not be silently dropped; it should still snap via the
    // unrestricted fallback rather than returning null.
    const fixOnOutbound = offset(50, 0.5);

    const result = snapToRoute(fixOnOutbound, route, 35, 999);

    expect(result).not.toBeNull();
    expect(result!.segmentIndex).toBe(0);
    expect(result!.bearing).toBeCloseTo(90, 0);
  });

  it('sanity check: the two legs really are ~6m/~4m from the noisy fix (proves the scenario is a genuine ambiguity, not a wide margin)', () => {
    const noisyFix = offset(50, 4);
    const onReturnLeg = offset(50, 10);
    const onOutboundLeg = offset(50, 0);

    const distToReturn = Math.abs(noisyFix.latitude - onReturnLeg.latitude) * METERS_PER_DEG_LAT;
    const distToOutbound = Math.abs(noisyFix.latitude - onOutboundLeg.latitude) * METERS_PER_DEG_LAT;

    expect(distToOutbound).toBeLessThan(distToReturn);
    expect(distToReturn - distToOutbound).toBeCloseTo(2, 0);
  });

  it('bearingDegrees itself confirms outbound/return are ~180 degrees apart (east vs west)', () => {
    const eastBearing = bearingDegrees(
      outbound0.latitude,
      outbound0.longitude,
      outbound1.latitude,
      outbound1.longitude,
    );
    const westBearing = bearingDegrees(
      return1.latitude,
      return1.longitude,
      return0.latitude,
      return0.longitude,
    );
    expect(eastBearing).toBeCloseTo(90, 0);
    expect(westBearing).toBeCloseTo(270, 0);
  });
});
