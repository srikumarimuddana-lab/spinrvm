/**
 * P1-9: Multi-stop E2E — rider store (R11)
 *
 * Pins the addStop / removeStop / updateStop store actions used in the
 * ride-booking flow. These actions manage a local stops[] array that is
 * sent with the ride-create request.
 *
 * Note: The store's addStop/removeStop manage pre-ride booking stops only.
 * Mid-trip stop mutations use the backend API directly from the UI layer
 * (POST /rides/{id}/stops). Backend tests for that path live in
 * backend/tests/test_p1_multi_stop.py.
 *
 * Code under test: rider-app/store/rideStore.ts addStop / removeStop / updateStop
 */

import { useRideStore } from '../rideStore';

jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(), get: jest.fn(), put: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: { id: 'user-abc' } })) },
}));

jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

const makeLocation = (address: string, lat = 52.13, lng = -106.67) => ({
  address,
  lat,
  lng,
  placeId: `place-${address}`,
});

beforeEach(() => {
  useRideStore.setState({ stops: [] });
  jest.clearAllMocks();
});

describe('rideStore — addStop / removeStop / updateStop (P1-9 / R11)', () => {
  describe('addStop', () => {
    it('appends a stop to an empty list', () => {
      const loc = makeLocation('450 Elm St');
      useRideStore.getState().addStop(loc);
      expect(useRideStore.getState().stops).toHaveLength(1);
      expect(useRideStore.getState().stops[0].address).toBe('450 Elm St');
    });

    it('appends multiple stops in order', () => {
      useRideStore.getState().addStop(makeLocation('Stop A'));
      useRideStore.getState().addStop(makeLocation('Stop B'));
      useRideStore.getState().addStop(makeLocation('Stop C'));

      const stops = useRideStore.getState().stops;
      expect(stops).toHaveLength(3);
      expect(stops.map((s) => s.address)).toEqual(['Stop A', 'Stop B', 'Stop C']);
    });

    it('preserves lat/lng precision', () => {
      useRideStore.getState().addStop(makeLocation('Precise Stop', 52.123456, -106.654321));
      const stop = useRideStore.getState().stops[0];
      expect(stop.lat).toBeCloseTo(52.123456, 5);
      expect(stop.lng).toBeCloseTo(-106.654321, 5);
    });

    it('does not mutate existing stops on add', () => {
      const first = makeLocation('First');
      useRideStore.getState().addStop(first);
      const snapshot = useRideStore.getState().stops;

      useRideStore.getState().addStop(makeLocation('Second'));
      // The original snapshot must be immutable
      expect(snapshot).toHaveLength(1);
      expect(useRideStore.getState().stops).toHaveLength(2);
    });
  });

  describe('removeStop', () => {
    it('removes a stop at the specified index', () => {
      useRideStore.getState().addStop(makeLocation('A'));
      useRideStore.getState().addStop(makeLocation('B'));
      useRideStore.getState().addStop(makeLocation('C'));

      useRideStore.getState().removeStop(1); // remove B

      const stops = useRideStore.getState().stops;
      expect(stops).toHaveLength(2);
      expect(stops.map((s) => s.address)).toEqual(['A', 'C']);
    });

    it('removes the first stop', () => {
      useRideStore.getState().addStop(makeLocation('First'));
      useRideStore.getState().addStop(makeLocation('Second'));

      useRideStore.getState().removeStop(0);

      expect(useRideStore.getState().stops[0].address).toBe('Second');
    });

    it('removes the last stop', () => {
      useRideStore.getState().addStop(makeLocation('Only'));
      useRideStore.getState().removeStop(0);
      expect(useRideStore.getState().stops).toHaveLength(0);
    });

    it('is a noop for an out-of-range index (no crash)', () => {
      useRideStore.getState().addStop(makeLocation('A'));
      // removeStop filters by index, so out-of-range removes nothing
      useRideStore.getState().removeStop(99);
      expect(useRideStore.getState().stops).toHaveLength(1);
    });
  });

  describe('updateStop', () => {
    it('replaces a stop at the given index', () => {
      useRideStore.getState().addStop(makeLocation('Original'));
      useRideStore.getState().updateStop(0, makeLocation('Updated'));
      expect(useRideStore.getState().stops[0].address).toBe('Updated');
    });

    it('replaces only the targeted index', () => {
      useRideStore.getState().addStop(makeLocation('A'));
      useRideStore.getState().addStop(makeLocation('B'));
      useRideStore.getState().addStop(makeLocation('C'));

      useRideStore.getState().updateStop(1, makeLocation('B-new'));

      const stops = useRideStore.getState().stops;
      expect(stops[0].address).toBe('A');
      expect(stops[1].address).toBe('B-new');
      expect(stops[2].address).toBe('C');
    });
  });

  describe('stops in fetchEstimates payload', () => {
    it('sends stops to backend on fetchEstimates', async () => {
      const mockPost = jest.fn().mockResolvedValue({ data: [] });
      const api = require('@shared/api/client').default;
      api.post = mockPost;

      useRideStore.setState({
        pickup: makeLocation('Pickup', 52.10, -106.60),
        dropoff: makeLocation('Dropoff', 52.20, -106.70),
        stops: [makeLocation('Stop A', 52.15, -106.65)],
      });

      await useRideStore.getState().fetchEstimates();

      expect(mockPost).toHaveBeenCalledWith(
        '/rides/estimate',
        expect.objectContaining({
          stops: expect.arrayContaining([
            expect.objectContaining({ address: 'Stop A' }),
          ]),
        }),
      );
    });
  });
});
