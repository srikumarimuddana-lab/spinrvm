/**
 * Unit tests for lib/androidAuto/carRoute.ts — the pure route-selection +
 * hand-off logic behind the Android Auto map. No native module / head unit.
 */
import fs from 'fs';
import path from 'path';
import {
  buildHandoffUrl,
  defaultNavButtons,
  extractPolyline,
  isNavState,
  resolveNavButtons,
  selectCarRoute,
} from '../carRoute';
import type { ActiveRide, RideState } from '../../../store/driverStore';

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
  it('targets the PICKUP before the trip starts, with NO route line drawn', () => {
    for (const s of ['navigating_to_pickup', 'arrived_at_pickup'] as RideState[]) {
      const route = selectCarRoute(s, makeRide());
      expect(route?.leg).toBe('pickup');
      expect(route?.destination).toEqual({ latitude: 52.13, longitude: -106.67 });
      expect(route?.destinationLabel).toBe('101 Pickup St');
      // The stored line is pickup→dropoff; it must NOT be drawn on the
      // driver→pickup leg (it would point away from the pickup hand-off).
      expect(route?.polyline).toEqual([]);
    }
  });

  it('targets the DROPOFF once in progress and exposes the FULL stored route line', () => {
    const route = selectCarRoute('trip_in_progress', makeRide());
    expect(route?.leg).toBe('dropoff');
    expect(route?.destination).toEqual({ latitude: 52.2, longitude: -106.6 });
    expect(route?.destinationLabel).toBe('202 Dropoff Ave');
    // The real geometry is kept in full — NOT reduced to endpoints — so the
    // shared <RouteLine> can draw the true road shape on the car surface.
    expect(route?.polyline).toHaveLength(3);
    expect(route?.polyline).toEqual([
      { latitude: 52.13, longitude: -106.67 },
      { latitude: 52.16, longitude: -106.64 },
      { latitude: 52.2, longitude: -106.6 },
    ]);
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

  it('uses the Google navigation intent on Android, comgooglemaps on iOS', () => {
    expect(buildHandoffUrl('google', dest, 'android')).toBe('google.navigation:q=52.2,-106.6');
    expect(buildHandoffUrl('google', dest, 'ios')).toBe(
      'comgooglemaps://?daddr=52.2,-106.6&directionsmode=driving'
    );
  });

  it('builds an Apple Maps driving-directions link (CarPlay)', () => {
    expect(buildHandoffUrl('apple', dest, 'ios')).toBe('maps://?daddr=52.2,-106.6&dirflg=d');
  });

  it('uses the waze:// scheme on iOS and the universal link on Android', () => {
    expect(buildHandoffUrl('waze', dest, 'ios')).toBe('waze://?ll=52.2,-106.6&navigate=yes');
    expect(buildHandoffUrl('waze', dest, 'android')).toBe(
      'https://waze.com/ul?ll=52.2,-106.6&navigate=yes'
    );
  });
});

describe('defaultNavButtons', () => {
  it('seeds only the guaranteed app per platform (no dead button)', () => {
    expect(defaultNavButtons('ios')).toEqual([{ id: 'nav-apple', provider: 'apple' }]);
    expect(defaultNavButtons('android')).toEqual([
      { id: 'nav-google', provider: 'google' },
      { id: 'nav-waze', provider: 'waze' },
    ]);
  });
});

describe('resolveNavButtons (CarPlay install detection)', () => {
  it('is static Google + Waze on Android (no probing)', async () => {
    const canOpen = jest.fn().mockResolvedValue(true);
    await expect(resolveNavButtons('android', canOpen)).resolves.toEqual([
      { id: 'nav-google', provider: 'google' },
      { id: 'nav-waze', provider: 'waze' },
    ]);
    expect(canOpen).not.toHaveBeenCalled();
  });

  it('adds Google Maps + Waze on iOS only when both are installed', async () => {
    const canOpen = jest.fn().mockResolvedValue(true);
    const buttons = await resolveNavButtons('ios', canOpen);
    expect(buttons.map((b) => b.provider)).toEqual(['apple', 'google', 'waze']);
    expect(canOpen).toHaveBeenCalledWith('comgooglemaps://');
    expect(canOpen).toHaveBeenCalledWith('waze://');
  });

  it('shows Google but not Waze when only Google Maps is installed', async () => {
    const canOpen = jest.fn((url: string) => Promise.resolve(url.startsWith('comgooglemaps')));
    const buttons = await resolveNavButtons('ios', canOpen);
    expect(buttons.map((b) => b.provider)).toEqual(['apple', 'google']);
  });

  it('falls back to Apple Maps only when nothing else is installed', async () => {
    const canOpen = jest.fn().mockResolvedValue(false);
    const buttons = await resolveNavButtons('ios', canOpen);
    expect(buttons.map((b) => b.provider)).toEqual(['apple']);
  });

  it('treats a canOpen rejection as "not installed" (never throws)', async () => {
    const canOpen = jest.fn().mockRejectedValue(new Error('blocked scheme'));
    await expect(resolveNavButtons('ios', canOpen)).resolves.toEqual([
      { id: 'nav-apple', provider: 'apple' },
    ]);
  });
});

describe('car surface route presentation contract (carSurface.tsx)', () => {
  const source = fs.readFileSync(path.resolve(__dirname, '..', 'carSurface.tsx'), 'utf8');

  it('draws the stored route through the shared RouteLine + RoutePins', () => {
    expect(source).toContain("RouteLine = require('@shared/components/RouteLine').RouteLine");
    expect(source).toContain("RoutePins = require('@shared/components/RoutePins').RoutePins");
    expect(source).toContain('<RouteLine path={livePath ?? route.polyline} />');
    expect(source).toContain('<RoutePins');
    expect(source).toContain('dropoff={route.leg === \'dropoff\' ? route.destination : null}');
  });

  it('drops the bespoke SPINR_RED Polyline + bare destination Marker', () => {
    expect(source).not.toContain('SPINR_RED');
    expect(source).not.toContain('<Polyline');
    // The lone destination <Marker> is replaced by the shared RoutePins.
    expect(source).not.toContain('<Marker coordinate={route.destination}');
  });
});
