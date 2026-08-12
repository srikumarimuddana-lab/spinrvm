/**
 * P1-7: Mid-trip restart restore — rider app (R14)
 *
 * Pins hydrateActiveRide(): when the rider's app is killed mid-trip and
 * relaunched, the store must restore the active ride from AsyncStorage
 * (optimistic) and then validate/correct against GET /rides/active.
 *
 * Code under test: rider-app/store/rideStore.ts::hydrateActiveRide
 * Key: @spinr:active_ride, TERMINAL_STATUSES, _persistRide
 */

import { useRideStore } from '../rideStore';
import api from '@shared/api/client';
import AsyncStorage from '@react-native-async-storage/async-storage';

jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    post: jest.fn(),
    get: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  hasAuthToken: jest.fn(() => true),
  SpinrApiError: class SpinrApiError extends Error {},
}));

jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: { id: 'user-abc' } })) },
}));

jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

const mockApi = api as jest.Mocked<typeof api>;
const mockAsyncStorage = AsyncStorage as jest.Mocked<typeof AsyncStorage>;

const ACTIVE_RIDE_KEY = '@spinr:active_ride';

const makeRide = (status: string, id = 'ride-789') => ({
  id,
  rider_id: 'user-abc',
  vehicle_type_id: 'vt-standard',
  status,
  pickup_address: '100 Queen St',
  pickup_lat: 43.65,
  pickup_lng: -79.38,
  dropoff_address: '200 King St',
  dropoff_lat: 43.64,
  dropoff_lng: -79.38,
  estimated_fare: 15.0,
  base_fare: '10.00',
  total_fare: '15.00',
  distance_km: 2.0,
  duration_minutes: 10,
  payment_method: 'card',
  payment_status: 'pending',
  pickup_otp: '1234',
  created_at: '2025-01-01T00:00:00Z',
});

beforeEach(() => {
  jest.clearAllMocks();
  useRideStore.setState({
    currentRide: null,
    currentDriver: null,
    isLoading: false,
    error: null,
  });
});

describe('hydrateActiveRide — mid-trip restart restore (P1-7 / R14)', () => {
  it('restores a driver_accepted ride from AsyncStorage and validates via API', async () => {
    const stored = makeRide('driver_accepted');
    const liveRide = { ...stored, driver: { id: 'drv-1', name: 'Jane' } };

    mockAsyncStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: stored, currentDriver: null }),
    );
    mockApi.get.mockResolvedValueOnce({
      status: 200, data: { active: true, ride: liveRide },
    });

    await useRideStore.getState().hydrateActiveRide();

    const state = useRideStore.getState();
    expect(state.currentRide?.id).toBe('ride-789');
    expect(state.currentRide?.status).toBe('driver_accepted');
  });

  it('restores an in_progress ride from AsyncStorage', async () => {
    const stored = makeRide('in_progress');

    mockAsyncStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: stored, currentDriver: null }),
    );
    mockApi.get.mockResolvedValueOnce({
      status: 200, data: { active: true, ride: stored },
    });

    await useRideStore.getState().hydrateActiveRide();

    expect(useRideStore.getState().currentRide?.status).toBe('in_progress');
  });

  it('does not restore a completed ride (terminal status)', async () => {
    const stored = makeRide('completed');

    mockAsyncStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: stored, currentDriver: null }),
    );

    await useRideStore.getState().hydrateActiveRide();

    expect(useRideStore.getState().currentRide).toBeNull();
    expect(mockAsyncStorage.removeItem).toHaveBeenCalledWith(ACTIVE_RIDE_KEY);
  });

  it('does not restore a cancelled ride (terminal status)', async () => {
    const stored = makeRide('cancelled');

    mockAsyncStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: stored, currentDriver: null }),
    );

    await useRideStore.getState().hydrateActiveRide();

    expect(useRideStore.getState().currentRide).toBeNull();
  });

  it('clears stale cache when server says the ride is no longer active', async () => {
    const stored = makeRide('in_progress');

    mockAsyncStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: stored, currentDriver: null }),
    );
    // Server says no active ride (completed while app was killed)
    mockApi.get.mockResolvedValueOnce({
      status: 200, data: { active: false, ride: null },
    });

    await useRideStore.getState().hydrateActiveRide();

    expect(useRideStore.getState().currentRide).toBeNull();
    expect(mockAsyncStorage.removeItem).toHaveBeenCalledWith(ACTIVE_RIDE_KEY);
  });

  it('clears stale cache when server returns a different ride id', async () => {
    const stored = makeRide('in_progress', 'ride-OLD');
    const serverRide = makeRide('searching', 'ride-NEW');

    mockAsyncStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: stored, currentDriver: null }),
    );
    mockApi.get.mockResolvedValueOnce({
      status: 200, data: { active: true, ride: serverRide },
    });

    await useRideStore.getState().hydrateActiveRide();

    // ride-OLD should be cleared; client shows no state (fetchActiveRide will
    // populate ride-NEW from a separate mount effect)
    expect(useRideStore.getState().currentRide).toBeNull();
    expect(mockAsyncStorage.removeItem).toHaveBeenCalledWith(ACTIVE_RIDE_KEY);
  });

  it('restores from cache optimistically when server is unreachable (offline)', async () => {
    const stored = makeRide('driver_accepted');

    mockAsyncStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: stored, currentDriver: null }),
    );
    mockApi.get.mockRejectedValueOnce(new Error('Network Error'));

    await useRideStore.getState().hydrateActiveRide();

    // Optimistic restore: cached data is shown while offline
    expect(useRideStore.getState().currentRide?.id).toBe('ride-789');
    expect(useRideStore.getState().currentRide?.status).toBe('driver_accepted');
  });

  it('is a noop when AsyncStorage is empty', async () => {
    mockAsyncStorage.getItem.mockResolvedValueOnce(null);

    await useRideStore.getState().hydrateActiveRide();

    expect(useRideStore.getState().currentRide).toBeNull();
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it('does not override an already-populated in-memory store', async () => {
    // fetchActiveRide ran first and already populated the store
    const liveRide = makeRide('in_progress');
    useRideStore.setState({ currentRide: liveRide });

    const cached = makeRide('driver_accepted'); // older/different state
    mockAsyncStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: cached, currentDriver: null }),
    );
    mockApi.get.mockResolvedValueOnce({
      status: 200, data: { active: true, ride: liveRide },
    });

    await useRideStore.getState().hydrateActiveRide();

    // In-memory state must not be clobbered by stale cache
    expect(useRideStore.getState().currentRide?.status).toBe('in_progress');
  });
});
