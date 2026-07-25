/**
 * createRide same-place guard bypass — the AI assistant's confirmed
 * same-location bookings pass { allowSamePlace: true } so the client-side
 * proximity guard (dropoffLikelyMisresolved, 100 m) honours the rider's
 * explicit confirmation; every other caller keeps the guard.
 */

import api from '@shared/api/client';

let useRideStore: typeof import('../store/rideStore').useRideStore;

jest.mock('@react-native-async-storage/async-storage', () => {
  const store: Record<string, string> = {};
  return {
    __esModule: true,
    default: {
      getItem: jest.fn((k: string) => Promise.resolve(store[k] ?? null)),
      setItem: jest.fn((k: string, v: string) => {
        store[k] = v;
        return Promise.resolve();
      }),
      removeItem: jest.fn((k: string) => {
        delete store[k];
        return Promise.resolve();
      }),
    },
  };
});

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn().mockResolvedValue({ data: {} }),
    post: jest.fn().mockResolvedValue({ data: { id: 'ride-1', status: 'searching' } }),
    patch: jest.fn().mockResolvedValue({ data: {} }),
    delete: jest.fn().mockResolvedValue({ data: {} }),
  },
}));

beforeAll(() => {
  ({ useRideStore } = require('../store/rideStore'));
});

// ~80 m apart with differing address strings — exactly what the guard exists
// to catch, and exactly what a rider-confirmed same-place trip looks like.
const PICKUP = { address: '4500 Gordon Rd, Regina, SK S4W 0B7', lat: 50.4079, lng: -104.6501 };
const DROPOFF = { address: '4500 Gordon Rd, Regina, SK S4S 6H7', lat: 50.40862, lng: -104.6501 };
const VEHICLE = { id: 'vt-1', name: 'Economy' };

beforeEach(() => {
  (api.post as jest.Mock).mockClear();
  useRideStore.setState({
    pickup: PICKUP,
    dropoff: DROPOFF,
    selectedVehicle: VEHICLE as never,
    stops: [],
    estimates: [],
    currentRide: null,
    scheduledTime: null,
  });
});

describe('createRide same-place guard', () => {
  it('throws for a too-close dropoff without allowSamePlace', async () => {
    await expect(useRideStore.getState().createRide('wallet')).rejects.toThrow(
      /too close to your pickup/i,
    );
    expect(api.post).not.toHaveBeenCalled();
  });

  it('books when the rider explicitly confirmed via allowSamePlace', async () => {
    const ride = await useRideStore
      .getState()
      .createRide('wallet', undefined, undefined, undefined, { allowSamePlace: true });
    expect((ride as { id: string }).id).toBe('ride-1');
    expect(api.post).toHaveBeenCalledWith('/rides', expect.anything(), expect.anything());
  });

  it('keeps the guard for a normal-distance trip even with allowSamePlace', async () => {
    useRideStore.setState({ dropoff: { address: '4325 Wakeling St', lat: 50.4497, lng: -104.5345 } });
    const ride = await useRideStore
      .getState()
      .createRide('wallet', undefined, undefined, undefined, { allowSamePlace: true });
    expect((ride as { id: string }).id).toBe('ride-1');
  });
});
