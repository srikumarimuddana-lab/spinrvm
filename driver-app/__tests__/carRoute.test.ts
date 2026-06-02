/**
 * Unit tests for car/carRoute.ts — the pure route-selection + hand-off logic
 * behind the Android Auto map. No native module / head unit required.
 */
import {
  buildHandoffUrl,
  extractPolyline,
  isNavState,
  navButtonsForPlatform,
  providerForButton,
  selectCarRoute,
} from '../car/carRoute';
import type { ActiveRide, RideState } from '../store/driverStore';

const makeRide = (overrides: Record<string, unknown> = {}): ActiveRide =>
  ({
    ride: {
      id: 'ride-1',
      pickup_address: '101 Pickup St',
      dropoff_address: '202 Dropoff Ave',
      pickup_lat: 52.13,
      pickup_lng: -106.67,
      dropoff_lat: 52.2,
      dropoff_lng: -106.6,
      planned_route_polyline: [
        [52.13, -106.67],
        [52.16, -106.64],
        [52.2, -106.6],
      ],
      ...overrides,
    },
    rider: { id: 'rider-1' },
    vehicle_type: { id: 'vt-1', name: 'Standard' },
  }) as unknown as ActiveRide;

describe('isNavState', () => {
  it('is true only for the three navigation states', () => {
    const truthy: RideState[] = ['navigating_to_pickup', 'arrived_at_pickup', 'trip_in_progress'];
    const falsy: RideState[] = ['idle', 'ride_offered', 'trip_completed'];
    truthy.forEach((s) => expect(isNavState(s)).toBe(true));
    falsy.forEach((s) => expect(isNavState(s)).toBe(false));
  });
});

describe('extractPolyline', () => {
  it('parses a [[lat,lng], …] line off activeRide.ride', () => {
    const line = extractPolyline(makeRide());
    expect(line).toHaveLength(3);
    expect(line[0]).toEqual({ latitude: 52.13, longitude: -106.67 });
    expect(line[2]).toEqual({ latitude: 52.2, longitude: -106.6 });
  });

  it('returns [] for null ride, missing line, or non-array junk', () => {
    expect(extractPolyline(null)).toEqual([]);
    expect(extractPolyline(makeRide({ planned_route_polyline: undefined }))).toEqual([]);
    expect(extractPolyline(makeRide({ planned_route_polyline: 'nope' }))).toEqual([]);
  });

  it('drops malformed points instead of rendering (0,0)', () => {
    const line = extractPolyline(
      makeRide({
        planned_route_polyline: [[52.1, -106.6], ['x', 1], [1], [52.2, -106.5]],
      })
    );
    expect(line).toEqual([
      { latitude: 52.1, longitude: -106.6 },
      { latitude: 52.2, longitude: -106.5 },
    ]);
  });
});

describe('selectCarRoute', () => {
  it('targets the PICKUP before the trip starts', () => {
    for (const s of ['navigating_to_pickup', 'arrived_at_pickup'] as RideState[]) {
      const route = selectCarRoute(s, makeRide());
      expect(route?.leg).toBe('pickup');
      expect(route?.destination).toEqual({ latitude: 52.13, longitude: -106.67 });
      expect(route?.destinationLabel).toBe('101 Pickup St');
      expect(route?.polyline).toHaveLength(3);
    }
  });

  it('targets the DROPOFF once in progress', () => {
    const route = selectCarRoute('trip_in_progress', makeRide());
    expect(route?.leg).toBe('dropoff');
    expect(route?.destination).toEqual({ latitude: 52.2, longitude: -106.6 });
    expect(route?.destinationLabel).toBe('202 Dropoff Ave');
  });

  it('returns null for non-nav states and a null ride', () => {
    expect(selectCarRoute('idle', makeRide())).toBeNull();
    expect(selectCarRoute('ride_offered', makeRide())).toBeNull();
    expect(selectCarRoute('trip_completed', makeRide())).toBeNull();
    expect(selectCarRoute('trip_in_progress', null)).toBeNull();
  });

  it('returns null when the destination coords are missing (no bogus pin)', () => {
    const route = selectCarRoute(
      'trip_in_progress',
      makeRide({ dropoff_lat: undefined, dropoff_lng: undefined })
    );
    expect(route).toBeNull();
  });

  it('still returns a route when the polyline is absent — marker + handoff only', () => {
    const route = selectCarRoute(
      'navigating_to_pickup',
      makeRide({ planned_route_polyline: undefined })
    );
    expect(route).not.toBeNull();
    expect(route?.polyline).toEqual([]);
  });
});

describe('buildHandoffUrl', () => {
  const dest = { latitude: 52.2, longitude: -106.6 };

  it('builds the Google turn-by-turn navigation intent (matches openNavigation)', () => {
    expect(buildHandoffUrl('google', dest)).toBe('google.navigation:q=52.2,-106.6');
  });

  it('builds an Apple Maps driving-directions link (CarPlay)', () => {
    expect(buildHandoffUrl('apple', dest)).toBe('maps://?daddr=52.2,-106.6&dirflg=d');
  });

  it('builds a Waze navigate deep link (both platforms)', () => {
    expect(buildHandoffUrl('waze', dest)).toBe('https://waze.com/ul?ll=52.2,-106.6&navigate=yes');
  });
});

describe('navButtonsForPlatform / providerForButton', () => {
  it('offers Apple Maps + Waze on iOS (CarPlay)', () => {
    expect(navButtonsForPlatform('ios')).toEqual([
      { id: 'nav-apple', provider: 'apple' },
      { id: 'nav-waze', provider: 'waze' },
    ]);
  });

  it('offers Google + Waze on Android (Android Auto)', () => {
    expect(navButtonsForPlatform('android')).toEqual([
      { id: 'nav-google', provider: 'google' },
      { id: 'nav-waze', provider: 'waze' },
    ]);
  });

  it('round-trips a pressed button id back to its provider', () => {
    expect(providerForButton('ios', 'nav-apple')).toBe('apple');
    expect(providerForButton('ios', 'nav-waze')).toBe('waze');
    expect(providerForButton('android', 'nav-google')).toBe('google');
  });

  it('falls back to the platform default for an unknown id', () => {
    expect(providerForButton('ios', 'mystery')).toBe('apple');
    expect(providerForButton('android', 'mystery')).toBe('google');
  });
});
