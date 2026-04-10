/**
 * rideStore tests (TST-002)
 * Covers the rider ride lifecycle: requesting → matched → in_progress → completed.
 * All network calls are mocked — no real HTTP occurs.
 */
import { act } from '@testing-library/react-native';

// Mock AsyncStorage (used by addRecentSearch / loadRecentSearches)
jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

// Mock the shared API client
jest.mock('@shared/api/client', () => ({
  default: {
    post: jest.fn(),
    get: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
}));

// Mock the auth store (imported transitively via @shared/store/authStore)
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: {
    getState: jest.fn(() => ({ user: { id: 'user-abc' } })),
  },
}));

// Mock expo-router
jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

import { useRideStore } from '../rideStore';
import api from '@shared/api/client';

const mockApi = api as jest.Mocked<typeof api>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const makeRide = (status: string, overrides: Record<string, unknown> = {}) => ({
  id: 'ride-456',
  rider_id: 'user-abc',
  vehicle_type_id: 'vt-1',
  pickup_address: '100 Queen St',
  pickup_lat: 43.6532,
  pickup_lng: -79.3832,
  dropoff_address: '200 King St',
  dropoff_lat: 43.6450,
  dropoff_lng: -79.3800,
  distance_km: 1.2,
  duration_minutes: 8,
  base_fare: 7.0,
  total_fare: 9.5,
  payment_method: 'card',
  status,
  pickup_otp: '8821',
  created_at: '2026-04-09T12:00:00Z',
  ...overrides,
});

const makeLocation = (address: string) => ({
  address,
  lat: 43.6532,
  lng: -79.3832,
});

const resetStore = () =>
  useRideStore.setState({
    pickup: null,
    dropoff: null,
    stops: [],
    estimates: [],
    nearbyDrivers: [],
    selectedVehicle: null,
    currentRide: null,
    currentDriver: null,
    savedAddresses: [],
    recentSearches: [],
    scheduledTime: null,
    scheduledRides: [],
    userLocation: null,
    availablePromos: [],
    appliedPromo: null,
    isLoading: false,
    error: null,
  });

beforeEach(() => {
  jest.clearAllMocks();
  resetStore();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('rideStore — location and vehicle selection', () => {
  test('setPickup stores location', () => {
    const loc = makeLocation('100 Queen St');
    act(() => useRideStore.getState().setPickup(loc));
    expect(useRideStore.getState().pickup).toEqual(loc);
  });

  test('setDropoff stores location', () => {
    const loc = makeLocation('200 King St');
    act(() => useRideStore.getState().setDropoff(loc));
    expect(useRideStore.getState().dropoff).toEqual(loc);
  });

  test('selectVehicle stores selected vehicle type', () => {
    const vehicle = { id: 'vt-1', name: 'Spinr X', description: 'Standard', icon: 'car', capacity: 4 };
    act(() => useRideStore.getState().selectVehicle(vehicle));
    expect(useRideStore.getState().selectedVehicle).toEqual(vehicle);
  });

  test('addStop and removeStop manage intermediate stops', () => {
    const stop1 = makeLocation('50 Bay St');
    const stop2 = makeLocation('75 Front St');

    act(() => {
      useRideStore.getState().addStop(stop1);
      useRideStore.getState().addStop(stop2);
    });
    expect(useRideStore.getState().stops).toHaveLength(2);

    act(() => {
      useRideStore.getState().removeStop(0);
    });
    expect(useRideStore.getState().stops).toHaveLength(1);
    expect(useRideStore.getState().stops[0]).toEqual(stop2);
  });
});

describe('rideStore — ride lifecycle', () => {
  test('createRide throws when pickup/dropoff/vehicle are missing', async () => {
    // No pickup, dropoff, or vehicle set
    await expect(
      act(async () => useRideStore.getState().createRide('card'))
    ).rejects.toThrow('Missing ride details');
  });

  test('createRide posts to /rides and stores currentRide (requesting → matched)', async () => {
    const vehicle = { id: 'vt-1', name: 'Spinr X', description: 'Standard', icon: 'car', capacity: 4 };
    useRideStore.setState({
      pickup: makeLocation('100 Queen St'),
      dropoff: makeLocation('200 King St'),
      selectedVehicle: vehicle,
    });

    const createdRide = makeRide('searching');
    mockApi.post.mockResolvedValueOnce({ data: createdRide, status: 201 } as any);

    let result: any;
    await act(async () => {
      result = await useRideStore.getState().createRide('card');
    });

    expect(mockApi.post).toHaveBeenCalledWith('/rides', expect.objectContaining({
      vehicle_type_id: 'vt-1',
      payment_method: 'card',
    }));
    expect(useRideStore.getState().currentRide).toEqual(createdRide);
    expect(result).toEqual(createdRide);
  });

  test('fetchActiveRide returns null when no active ride', async () => {
    mockApi.get.mockResolvedValueOnce({ data: { active: false }, status: 200 } as any);

    let result: any;
    await act(async () => {
      result = await useRideStore.getState().fetchActiveRide();
    });

    expect(result).toBeNull();
    expect(useRideStore.getState().currentRide).toBeNull();
  });

  test('fetchActiveRide populates currentRide when a ride is active', async () => {
    const activeRide = makeRide('driver_accepted');
    mockApi.get.mockResolvedValueOnce({
      data: { active: true, ride: activeRide },
      status: 200,
    } as any);

    await act(async () => {
      await useRideStore.getState().fetchActiveRide();
    });

    expect(useRideStore.getState().currentRide).toEqual(activeRide);
  });

  test('cancelRide posts cancel and clears currentRide + currentDriver', async () => {
    useRideStore.setState({
      currentRide: makeRide('driver_accepted') as any,
      currentDriver: { id: 'drv-1', name: 'Bob' } as any,
    });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    await act(async () => {
      await useRideStore.getState().cancelRide();
    });

    expect(mockApi.post).toHaveBeenCalledWith('/rides/ride-456/cancel');
    expect(useRideStore.getState().currentRide).toBeNull();
    expect(useRideStore.getState().currentDriver).toBeNull();
  });

  test('startRide updates currentRide status to in_progress', async () => {
    useRideStore.setState({ currentRide: makeRide('driver_arrived') as any });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    await act(async () => {
      await useRideStore.getState().startRide();
    });

    expect(useRideStore.getState().currentRide?.status).toBe('in_progress');
    expect(mockApi.post).toHaveBeenCalledWith('/rides/ride-456/start');
  });

  test('completeRide posts complete and stores returned ride data', async () => {
    const completedRide = makeRide('completed', { total_fare: 9.5 });
    useRideStore.setState({ currentRide: makeRide('in_progress') as any });

    mockApi.post.mockResolvedValueOnce({ data: completedRide, status: 200 } as any);

    let result: any;
    await act(async () => {
      result = await useRideStore.getState().completeRide();
    });

    expect(mockApi.post).toHaveBeenCalledWith('/rides/ride-456/complete');
    expect(useRideStore.getState().currentRide?.status).toBe('completed');
    expect(result).toEqual(completedRide);
  });

  test('clearRide nulls currentRide and currentDriver without touching pickup/dropoff', () => {
    const pickup = makeLocation('100 Queen St');
    const dropoff = makeLocation('200 King St');
    useRideStore.setState({
      currentRide: makeRide('in_progress') as any,
      currentDriver: { id: 'drv-1' } as any,
      pickup,
      dropoff,
      error: 'some error',
    });

    act(() => useRideStore.getState().clearRide());

    const state = useRideStore.getState();
    expect(state.currentRide).toBeNull();
    expect(state.currentDriver).toBeNull();
    expect(state.error).toBeNull();
    // pickup/dropoff deliberately preserved
    expect(state.pickup).toEqual(pickup);
    expect(state.dropoff).toEqual(dropoff);
  });
});

describe('rideStore — recent searches', () => {
  test('addRecentSearch prepends and deduplicates', () => {
    const loc1 = makeLocation('Home');
    const loc2 = makeLocation('Work');

    act(() => {
      useRideStore.getState().addRecentSearch(loc1);
      useRideStore.getState().addRecentSearch(loc2);
      // Re-add loc1 — should move it to front, not duplicate
      useRideStore.getState().addRecentSearch(loc1);
    });

    const { recentSearches } = useRideStore.getState();
    expect(recentSearches).toHaveLength(2);
    expect(recentSearches[0]).toEqual(loc1);
    expect(recentSearches[1]).toEqual(loc2);
  });

  test('clearRecentSearches empties the list', () => {
    act(() => {
      useRideStore.getState().addRecentSearch(makeLocation('A'));
      useRideStore.getState().addRecentSearch(makeLocation('B'));
    });
    expect(useRideStore.getState().recentSearches).toHaveLength(2);

    act(() => useRideStore.getState().clearRecentSearches());
    expect(useRideStore.getState().recentSearches).toHaveLength(0);
  });
});
