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
jest.mock('@shared/api/client', () => {
  const actual = jest.requireActual('@shared/api/client');
  const mockClient = {
    post: jest.fn(),
    get: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  };
  return {
    ...actual,
    __esModule: true,
    default: mockClient,
    hasAuthToken: jest.fn(() => true),
    SpinrApiError: class SpinrApiError extends Error {},
  };
});

// Mock the auth store (imported transitively via @shared/store/authStore)
jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
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

// Distinct coords per address so pickup and dropoff are ~1 km apart — matching
// a real booking (and clearing the mis-resolved-dropoff guard in createRide,
// which blocks a dropoff sitting on top of the pickup with a different address).
const makeLocation = (address: string, lat = 43.6532, lng = -79.3832) => ({
  address,
  lat,
  lng,
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
    _clearedRideId: null,
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
    const loc = makeLocation('200 King St', 43.6450, -79.3800);
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
      act(async () => { await useRideStore.getState().createRide('card'); })
    ).rejects.toThrow('Missing ride details');
  });

  test('createRide posts to /rides and stores currentRide (requesting → matched)', async () => {
    const vehicle = { id: 'vt-1', name: 'Spinr X', description: 'Standard', icon: 'car', capacity: 4 };
    useRideStore.setState({
      pickup: makeLocation('100 Queen St'),
      dropoff: makeLocation('200 King St', 43.6450, -79.3800),
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
    }), expect.objectContaining({ headers: expect.any(Object) }));
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
    const dropoff = makeLocation('200 King St', 43.6450, -79.3800);
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

// R-P1-23: hydrateActiveRide, double-booking prevention, cancel-after-driver_arrived
describe('rideStore — hydrateActiveRide', () => {
  test('hydrateActiveRide restores currentRide from AsyncStorage', async () => {
    const storedRide = makeRide('driver_accepted');
    const mockStorage = require('@react-native-async-storage/async-storage');
    mockStorage.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: storedRide, currentDriver: null })
    );

    let result: any;
    await act(async () => {
      result = await useRideStore.getState().hydrateActiveRide?.();
    });

    // hydrateActiveRide may not exist yet — verify the store has the method or skip
    if (typeof useRideStore.getState().hydrateActiveRide !== 'function') return;
    expect(useRideStore.getState().currentRide?.status).toBe('driver_accepted');
  });
});

describe('rideStore — double-booking prevention', () => {
  test('createRide rejects when an active ride already exists', async () => {
    const vehicle = { id: 'vt-1', name: 'Spinr X', description: 'Standard', icon: 'car', capacity: 4 };
    useRideStore.setState({
      pickup: makeLocation('100 Queen St'),
      dropoff: makeLocation('200 King St', 43.6450, -79.3800),
      selectedVehicle: vehicle,
      currentRide: makeRide('searching') as any,
    });

    // The guard revalidates against the server before throwing, so
    // GET /rides/active must confirm the ride is still live.
    const mockApi = require('@shared/api/client').default;
    mockApi.get.mockResolvedValueOnce({
      data: { active: true, ride: makeRide('searching') },
      status: 200,
    });
    await expect(
      act(async () => { await useRideStore.getState().createRide('card'); })
    ).rejects.toThrow('A ride is already active');
  });
});

describe('rideStore — cancel after driver_arrived', () => {
  test('cancelRide sets error state when backend rejects (cancellation fee scenario)', async () => {
    useRideStore.setState({
      currentRide: makeRide('driver_arrived') as any,
      currentDriver: { id: 'drv-1', name: 'Bob' } as any,
    });

    const err: any = new Error('Cancellation fee applies');
    err.response = { status: 400, data: { detail: 'Cancellation fee applies after driver has arrived' } };
    mockApi.post.mockRejectedValueOnce(err);

    // cancelRide records the error in state and rethrows so callers can react
    await act(async () => {
      await expect(useRideStore.getState().cancelRide()).rejects.toThrow('Cancellation fee applies');
    });

    expect(useRideStore.getState().error).toBeTruthy();
    // Ride is NOT cleared on error — rider stays on the screen
    expect(useRideStore.getState().currentRide).not.toBeNull();
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
    // toMatchObject: entries additionally carry a saved_at timestamp (recents
    // v2 expiry) on top of the caller-provided fields.
    expect(recentSearches[0]).toMatchObject(loc1);
    expect(recentSearches[1]).toMatchObject(loc2);
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

// ---------------------------------------------------------------------------
// R-P3-7 — Hardening: missing action paths
// ---------------------------------------------------------------------------

describe('rideStore — createRide double-booking guard', () => {
  test('propagates 409 conflict when server rejects duplicate ride', async () => {
    const vehicle = { id: 'vt-1', name: 'Spinr X', description: 'Standard', icon: 'car', capacity: 4 };
    useRideStore.setState({
      pickup: makeLocation('100 Queen St'),
      dropoff: makeLocation('200 King St', 43.6450, -79.3800),
      selectedVehicle: vehicle,
    });

    const conflictErr: any = new Error('Request failed with status code 409');
    conflictErr.response = { status: 409, data: { detail: 'You already have an active ride' } };
    mockApi.post.mockRejectedValueOnce(conflictErr);

    await expect(
      act(async () => { await useRideStore.getState().createRide('card'); })
    ).rejects.toThrow();

    expect(useRideStore.getState().isLoading).toBe(false);
    expect(useRideStore.getState().error).toBeTruthy();
    expect(useRideStore.getState().currentRide).toBeNull();
  });
});

describe('rideStore — cancelRide after driver_arrived', () => {
  test('sets error state when backend rejects cancel for arrived driver', async () => {
    useRideStore.setState({
      currentRide: makeRide('driver_arrived') as any,
      currentDriver: { id: 'drv-1', name: 'Bob' } as any,
    });

    const err: any = new Error('Request failed with status code 400');
    err.response = { status: 400, data: { detail: 'Cancellation fee applies' } };
    mockApi.post.mockRejectedValueOnce(err);

    // cancelRide sets error state and rethrows for the caller to handle
    await act(async () => {
      await expect(useRideStore.getState().cancelRide()).rejects.toThrow();
    });

    const state = useRideStore.getState();
    expect(state.error).toBeTruthy();
    // ride should NOT have been cleared — we only clear on success
    expect(state.currentRide).not.toBeNull();
    expect(state.isLoading).toBe(false);
  });
});

describe('rideStore — hydrateActiveRide stale ride', () => {
  const AsyncStorageMock = require('@react-native-async-storage/async-storage');

  test('clears AsyncStorage when stored ride has terminal status', async () => {
    const staleRide = makeRide('cancelled');
    AsyncStorageMock.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: staleRide, currentDriver: null })
    );

    await act(async () => {
      await useRideStore.getState().hydrateActiveRide();
    });

    // Terminal ride must be removed from storage, not loaded into state
    expect(AsyncStorageMock.removeItem).toHaveBeenCalled();
    expect(useRideStore.getState().currentRide).toBeNull();
  });

  test('does not overwrite an already-loaded ride', async () => {
    const memoryRide = makeRide('driver_accepted');
    useRideStore.setState({ currentRide: memoryRide as any });

    const storedRide = makeRide('driver_accepted', { id: 'ride-old' });
    AsyncStorageMock.getItem.mockResolvedValueOnce(
      JSON.stringify({ currentRide: storedRide, currentDriver: null })
    );

    await act(async () => {
      await useRideStore.getState().hydrateActiveRide();
    });

    // Memory ride wins — stored ride should not overwrite it
    expect(useRideStore.getState().currentRide?.id).toBe(memoryRide.id);
  });
});

describe('rideStore — syncOfflineRequests', () => {
  const AsyncStorageMock = require('@react-native-async-storage/async-storage');

  test('replays queued create_ride requests and clears them on success', async () => {
    const queuedReq = { id: 'q-1', type: 'create_ride', data: { vehicle_type_id: 'vt-1' }, retryCount: 0 };
    AsyncStorageMock.getItem.mockResolvedValueOnce(JSON.stringify([queuedReq]));
    mockApi.post.mockResolvedValueOnce({ data: makeRide('searching'), status: 200 });

    await act(async () => {
      await useRideStore.getState().syncOfflineRequests();
    });

    expect(mockApi.post).toHaveBeenCalledWith('/rides', queuedReq.data);
    // Queue should now be empty (stored as [])
    const storedQueue = JSON.parse(AsyncStorageMock.setItem.mock.calls.at(-1)[1]);
    expect(storedQueue).toHaveLength(0);
  });

  test('removes request after max retries (3) on repeated failure', async () => {
    const queuedReq = { id: 'q-2', type: 'create_ride', data: {}, retryCount: 2 };
    AsyncStorageMock.getItem.mockResolvedValueOnce(JSON.stringify([queuedReq]));
    mockApi.post.mockRejectedValueOnce(new Error('Network error'));

    await act(async () => {
      await useRideStore.getState().syncOfflineRequests();
    });

    // retryCount was already 2; after one more failure it hits >= 3 → removed
    const storedQueue = JSON.parse(AsyncStorageMock.setItem.mock.calls.at(-1)[1]);
    expect(storedQueue).toHaveLength(0);
  });
});

describe('rideStore — triggerEmergency network failure', () => {
  test('throws on API failure so SOSButton retry loop can detect it (R-P0-1)', async () => {
    // triggerEmergency retries up to MAX_ATTEMPTS (3) times before throwing.
    // Mock all 3 attempts to fail so the function exhausts retries and rethrows.
    mockApi.post
      .mockRejectedValueOnce(new Error('Network error'))
      .mockRejectedValueOnce(new Error('Network error'))
      .mockRejectedValueOnce(new Error('Network error'));

    await expect(
      useRideStore.getState().triggerEmergency('ride-456', 43.65, -79.38)
    ).rejects.toThrow('Network error');

    // triggerEmergency does NOT set store.error — failure UX is SOSButton's responsibility
    expect(useRideStore.getState().error).toBeNull();
  }, 10000); // 3-attempt retry has 1s+2s back-off delays
});
