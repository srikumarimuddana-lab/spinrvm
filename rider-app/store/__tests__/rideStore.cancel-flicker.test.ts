/**
 * Cancel-during-search toast flicker (rider app)
 *
 * When a rider cancels while "searching for driver", three paths used to fire
 * in a tight window — the local cancel button, the status-watch effect, and the
 * server's ride_cancelled WS echo — each re-showing the toast and re-navigating.
 * Because the toast restarts its enter animation on every new id, the rider saw
 * the banner flicker.
 *
 * These tests pin the store-level invariants the call-site fix depends on:
 *   1. cancelRide() records the cancelled ride in _clearedRideId.
 *   2. A trailing clearRide() (performCancel calls both) preserves it.
 *   3. fetchActiveRide() treats a just-cancelled ride as inactive, so the home
 *      useFocusEffect does not bounce back into the searching screen.
 * The useRiderSocket ride_cancelled guard reads the same _clearedRideId.
 *
 * Code under test: rider-app/store/rideStore.ts::{cancelRide, clearRide, fetchActiveRide}
 */

import { useRideStore } from '../rideStore';
import api from '@shared/api/client';

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
    _clearedRideId: null,
  });
});

describe('cancel-during-search flicker — store invariants', () => {
  it('cancelRide records the cancelled ride id in _clearedRideId', async () => {
    useRideStore.setState({ currentRide: makeRide('searching') as any });
    mockApi.post.mockResolvedValueOnce({ status: 200, data: {} } as any);

    await useRideStore.getState().cancelRide();

    const state = useRideStore.getState();
    expect(state.currentRide).toBeNull();
    expect(state._clearedRideId).toBe('ride-789');
  });

  it('clearRide() after cancelRide preserves _clearedRideId (performCancel order)', async () => {
    useRideStore.setState({ currentRide: makeRide('searching') as any });
    mockApi.post.mockResolvedValueOnce({ status: 200, data: {} } as any);

    // performCancel(): await cancelRide(); clearRide();
    await useRideStore.getState().cancelRide();
    useRideStore.getState().clearRide();

    // Without the fallback, the trailing clearRide() (currentRide already null)
    // would reset this to null and re-enable the duplicate WS handling.
    expect(useRideStore.getState()._clearedRideId).toBe('ride-789');
  });

  it('fetchActiveRide treats a just-cancelled ride as inactive (no home bounce)', async () => {
    useRideStore.setState({ _clearedRideId: 'ride-789' });
    // Server read-after-write lag: /rides/active still reports it searching.
    mockApi.get.mockResolvedValueOnce({
      status: 200,
      data: { active: true, ride: makeRide('searching', 'ride-789') },
    } as any);

    const result = await useRideStore.getState().fetchActiveRide();

    expect(result).toBeNull();
    expect(useRideStore.getState().currentRide).toBeNull();
  });

  it('fetchActiveRide still populates a genuinely different active ride', async () => {
    useRideStore.setState({ _clearedRideId: 'ride-OLD' });
    mockApi.get.mockResolvedValueOnce({
      status: 200,
      data: { active: true, ride: makeRide('searching', 'ride-NEW') },
    } as any);

    const result = await useRideStore.getState().fetchActiveRide();

    expect(result?.active).toBe(true);
    expect(useRideStore.getState().currentRide?.id).toBe('ride-NEW');
    // Guard is reset so a future cancel of ride-NEW notifies normally.
    expect(useRideStore.getState()._clearedRideId).toBeNull();
  });
});
