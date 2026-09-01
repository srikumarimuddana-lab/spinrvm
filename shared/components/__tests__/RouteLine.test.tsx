import { trimTraveled, type RoutePoint } from '../RouteLine';

// react-native-maps requires native modules Jest can't load — RouteLine only
// imports `Polyline` from it, unused by trimTraveled itself, so a minimal
// stub is enough (same pattern as driver-app's CarMarker.test.tsx).
jest.mock('react-native-maps', () => ({
  __esModule: true,
  Polyline: () => null,
}));

// A short, roughly-straight road heading east, one point every ~11m
// (0.0001° longitude at this latitude) — realistic GPS-fix spacing.
const ROAD: RoutePoint[] = [
  { latitude: 50.445, longitude: -104.62 },
  { latitude: 50.445, longitude: -104.6199 },
  { latitude: 50.445, longitude: -104.6198 },
  { latitude: 50.445, longitude: -104.6197 },
  { latitude: 50.445, longitude: -104.6196 },
];

describe('trimTraveled', () => {
  it('returns the path unchanged when no vehicle position is given', () => {
    expect(trimTraveled(ROAD, null)).toEqual(ROAD);
    expect(trimTraveled(ROAD, undefined)).toEqual(ROAD);
  });

  it('returns the path unchanged when it has fewer than 2 points', () => {
    const single = [ROAD[0]];
    expect(trimTraveled(single, { latitude: 50.445, longitude: -104.6199 })).toEqual(single);
  });

  it('drops the portion behind the vehicle and keeps the snapped point + remainder', () => {
    // Vehicle sitting almost exactly on the 3rd point (index 2) — should
    // trim everything before it and start the line from there.
    const vehicle = { latitude: 50.445, longitude: -104.61979 };
    const trimmed = trimTraveled(ROAD, vehicle);
    // First point of the trimmed line is the snapped point (near ROAD[2]),
    // not the original ROAD[0] — the traveled prefix is gone.
    expect(trimmed[0].longitude).toBeCloseTo(ROAD[2].longitude, 3);
    expect(trimmed.length).toBeLessThan(ROAD.length);
    // Everything still ahead of the vehicle (indices 3, 4) survives intact.
    expect(trimmed[trimmed.length - 1]).toEqual(ROAD[ROAD.length - 1]);
  });

  it('renders the whole path unchanged when the vehicle is far off-route (detour/stale fix)', () => {
    // ~1km away — well outside snapToRoute's default 35m tolerance.
    const farAway = { latitude: 50.455, longitude: -104.62 };
    expect(trimTraveled(ROAD, farAway)).toEqual(ROAD);
  });

  it('keeps the whole path when the vehicle sits at the very start (nothing traveled yet)', () => {
    const atStart = { latitude: 50.445, longitude: -104.62 };
    const trimmed = trimTraveled(ROAD, atStart);
    expect(trimmed.length).toBe(ROAD.length);
  });
});
