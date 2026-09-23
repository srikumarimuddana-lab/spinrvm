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
import { registerLogoutCallback } from '@shared/store/authStore';

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
// Store registration runs at import time, before beforeEach clears mock calls.
const logoutRideStore = (registerLogoutCallback as jest.Mock).mock.calls[0][0] as () => void;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

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
  mockApi.get.mockReset();
  mockApi.post.mockReset();
  useRideStore.setState({
    currentRide: null,
    currentDriver: null,
    isLoading: false,
    error: null,
    _clearedRideId: null,
  });
});

describe('cancel-during-search flicker — store invariants', () => {
  it.each(['inactive', '404', 'previous ride'] as const)(
    'ignores an older %s response after a newer check confirms the new ride',
    async (kind) => {
      useRideStore.setState({ currentRide: makeRide('searching', 'ride-B') as any, _clearedRideId: 'ride-A' });
      const oldResponse = deferred<any>();
      const newResponse = deferred<any>();
      mockApi.get.mockReturnValueOnce(oldResponse.promise).mockReturnValueOnce(newResponse.promise);
      const olderCheck = useRideStore.getState().fetchActiveRide();
      const newerCheck = useRideStore.getState().fetchActiveRide();

      newResponse.resolve({ data: { active: true, ride: makeRide('searching', 'ride-B') } });
      await newerCheck;
      if (kind === '404') oldResponse.reject(Object.assign(new Error('Not found'), { response: { status: 404 } }));
      else oldResponse.resolve({ data: kind === 'inactive'
        ? { active: false }
        : { active: true, ride: makeRide('searching', 'ride-A') } });

      expect(await olderCheck).toBeNull();
      expect(useRideStore.getState().currentRide?.id).toBe('ride-B');
      expect(useRideStore.getState()._clearedRideId).toBeNull();
    },
  );

  it('does not erase a booking created after an active-ride check started', async () => {
    const response = deferred<any>();
    mockApi.get.mockReturnValueOnce(response.promise);
    const check = useRideStore.getState().fetchActiveRide();
    useRideStore.setState({
      pickup: { address: '100 Queen St', lat: 43.65, lng: -79.38 },
      dropoff: { address: '200 King St', lat: 43.66, lng: -79.39 },
      selectedVehicle: { id: 'vt-1', name: 'Economy' } as any,
    });
    mockApi.post.mockResolvedValueOnce({ status: 201, data: makeRide('searching', 'ride-B') } as any);
    await useRideStore.getState().createRide('card');
    response.resolve({ data: { active: false } });
    await check;
    expect(useRideStore.getState().currentRide?.id).toBe('ride-B');
  });

  it('does not resurrect a ride after clearRide overtakes the request', async () => {
    const response = deferred<any>();
    mockApi.get.mockReturnValueOnce(response.promise);
    const check = useRideStore.getState().fetchActiveRide();
    // Even null -> null clears retire outstanding work (e.g. session cleanup).
    useRideStore.getState().clearRide();
    response.resolve({ data: { active: true, ride: makeRide('searching', 'ride-A') } });
    expect(await check).toBeNull();
    expect(useRideStore.getState().currentRide).toBeNull();
  });

  it('does not adopt an outstanding active-ride response after logout', async () => {
    const response = deferred<any>();
    mockApi.get.mockReturnValueOnce(response.promise);
    const check = useRideStore.getState().fetchActiveRide();
    logoutRideStore();
    response.resolve({ data: { active: true, ride: makeRide('searching', 'ride-A') } });
    expect(await check).toBeNull();
    expect(useRideStore.getState().currentRide).toBeNull();
  });

  it('does not roll back a newer websocket status for the same ride', async () => {
    useRideStore.setState({ currentRide: makeRide('searching', 'ride-B') as any });
    const response = deferred<any>();
    mockApi.get.mockReturnValueOnce(response.promise);
    const check = useRideStore.getState().fetchActiveRide();
    useRideStore.getState().applyRideStatusFromWS('ride-B', 'driver_accepted');
    response.resolve({ data: { active: true, ride: makeRide('searching', 'ride-B') } });
    expect(await check).toBeNull();
    expect(useRideStore.getState().currentRide?.status).toBe('driver_accepted');
  });

  it('still clears a stale local ride on a fresh authoritative inactive check', async () => {
    useRideStore.setState({ currentRide: makeRide('searching', 'ride-B') as any, _clearedRideId: null });
    mockApi.get.mockResolvedValueOnce({ data: { active: false } } as any);
    await useRideStore.getState().fetchActiveRide();
    expect(useRideStore.getState().currentRide).toBeNull();
  });

  it('does not overwrite a newer websocket driver position with an active-ride response', async () => {
    const driver = { id: 'driver-B', lat: 43.65, lng: -79.38 } as any;
    useRideStore.setState({
      currentRide: makeRide('driver_accepted', 'ride-B') as any,
      currentDriver: driver,
      _lastDriverFix: null,
    });
    const response = deferred<any>();
    mockApi.get.mockReturnValueOnce(response.promise);
    const check = useRideStore.getState().fetchActiveRide();
    useRideStore.getState().updateDriverLocation(43.66, -79.39, 10, 90, 60, {
      rideId: 'ride-B', driverId: 'driver-B', capturedAt: new Date().toISOString(),
    });
    response.resolve({ data: { active: true, ride: { ...makeRide('driver_accepted', 'ride-B'), driver } } });
    expect(await check).toBeNull();
    expect(useRideStore.getState().currentDriver).toMatchObject({ lat: 43.66, lng: -79.39 });
  });

  it('does not create another ride when its active check was overtaken by a websocket update', async () => {
    useRideStore.setState({
      currentRide: makeRide('searching', 'ride-B') as any,
      pickup: { address: '100 Queen St', lat: 43.65, lng: -79.38 },
      dropoff: { address: '200 King St', lat: 43.66, lng: -79.39 },
      selectedVehicle: { id: 'vt-1', name: 'Economy' } as any,
    });
    const response = deferred<any>();
    mockApi.get.mockReturnValueOnce(response.promise);
    // If a second booking is attempted, make it succeed so the assertion catches it.
    mockApi.post.mockResolvedValueOnce({ status: 201, data: makeRide('searching', 'ride-C') } as any);
    const booking = useRideStore.getState().createRide('card');
    useRideStore.getState().applyRideStatusFromWS('ride-B', 'driver_accepted');
    response.resolve({ data: { active: false } });
    await expect(booking).rejects.toThrow('A ride is already active');
    expect(mockApi.post).not.toHaveBeenCalled();
    expect(useRideStore.getState().currentRide?.id).toBe('ride-B');
  });

  it('reconciles a terminal update before permitting a new booking', async () => {
    useRideStore.setState({
      currentRide: makeRide('searching', 'ride-B') as any, _clearedRideId: 'ride-A',
      pickup: { address: '100 Queen St', lat: 43.65, lng: -79.38 },
      dropoff: { address: '200 King St', lat: 43.66, lng: -79.39 },
      selectedVehicle: { id: 'vt-1', name: 'Economy' } as any,
    });
    const response = deferred<any>();
    mockApi.get.mockReturnValueOnce(response.promise).mockResolvedValueOnce({ data: { active: false } } as any);
    mockApi.post.mockResolvedValueOnce({ status: 201, data: makeRide('searching', 'ride-C') } as any);
    const booking = useRideStore.getState().createRide('card');
    useRideStore.getState().applyRideStatusFromWS('ride-B', 'cancelled');
    response.resolve({ data: { active: false } });
    expect(await booking).toMatchObject({ id: 'ride-C' });
    expect(mockApi.get).toHaveBeenCalledTimes(2);
    expect(useRideStore.getState().currentRide?.id).toBe('ride-C');
  });

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

  it('createRide preserves _clearedRideId from the prior cancel', async () => {
    useRideStore.setState({
      _clearedRideId: 'ride-1',
      currentRide: null,
      pickup: { address: '100 Queen St', lat: 43.65, lng: -79.38 },
      dropoff: { address: '200 King St', lat: 43.66, lng: -79.39 },
      selectedVehicle: { id: 'vt-1', name: 'Economy', description: '', icon: 'car', capacity: 4 },
    });
    mockApi.post.mockResolvedValueOnce({
      status: 201,
      data: makeRide('searching', 'ride-2'),
    } as any);

    await useRideStore.getState().createRide('card');

    expect(useRideStore.getState().currentRide?.id).toBe('ride-2');
    expect(useRideStore.getState()._clearedRideId).toBe('ride-1');
  });

  it('fetchActiveRide inactive does not clear a newer local ride', async () => {
    useRideStore.setState({
      currentRide: makeRide('searching', 'ride-NEW') as any,
      _clearedRideId: 'ride-OLD',
    });
    mockApi.get.mockResolvedValueOnce({
      status: 200,
      data: { active: false },
    } as any);

    const result = await useRideStore.getState().fetchActiveRide();

    expect(result).toBeNull();
    expect(useRideStore.getState().currentRide?.id).toBe('ride-NEW');
    expect(useRideStore.getState()._clearedRideId).toBe('ride-OLD');
  });
});
