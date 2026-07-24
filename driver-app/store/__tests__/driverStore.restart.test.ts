/**
 * P1-7: Mid-trip restart restore — driver app (D12)
 *
 * Pins hydrateDriverRideState(): when the driver's app is killed mid-trip
 * and relaunched, the store must restore rideState + activeRide from
 * AsyncStorage so the trip UI shows immediately on cold start.
 * fetchActiveRide() (called immediately after by useDriverDashboard) then
 * validates and updates the state against the server.
 *
 * Code under test: driver-app/store/driverStore.ts::hydrateDriverRideState
 * Key: @spinr:driver_active_ride, DRIVER_TERMINAL_STATES, _persistDriverState
 */

jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    setItem: jest.fn(() => Promise.resolve()),
    getItem: jest.fn(() => Promise.resolve(null)),
    removeItem: jest.fn(() => Promise.resolve()),
  },
}));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { rideOffer: { countdownSeconds: 15 } },
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    post: jest.fn(),
    get: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
    put: jest.fn(),
  },
}));

jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

// driverStore imports tripLocationRecorder, which imports expo-location —
// mocked to avoid pulling in the real native module (crashes Jest's Expo
// Winter-runtime polyfill outside a real device/simulator context).
jest.mock('../../utils/tripLocationRecorder', () => ({
  tripLocationRecorder: {
    startRide: jest.fn(),
    captureCompletionFix: jest.fn(),
    applyAcknowledgement: jest.fn(),
    closeRide: jest.fn(),
    flushPendingWithTimeout: jest.fn(),
  },
}));

jest.mock('../../utils/tripLocationTransport', () => ({
  apiLocationBatchTransport: jest.fn(),
}));

import AsyncStorage from '@react-native-async-storage/async-storage';
import { useDriverStore } from '../driverStore';

const DRIVER_RIDE_KEY = '@spinr:driver_active_ride';

const makeActiveRide = (overrides: Record<string, unknown> = {}) => ({
  ride: {
    id: (overrides.id as string) || 'ride-drv-001',
    status: 'in_progress',
    rider_id: 'rider-abc',
    pickup_address: '100 Queen St',
    pickup_lat: 43.65,
    pickup_lng: -79.38,
    dropoff_address: '200 King St',
    dropoff_lat: 43.64,
    dropoff_lng: -79.38,
    total_fare: '15.00',
    distance_km: 2.0,
    duration_minutes: 10,
    created_at: '2024-01-01',
    ...overrides,
  },
  rider: { id: 'rider-abc' },
  vehicle_type: { id: 'vt-1', name: 'Standard' },
});

const resetStore = () =>
  useDriverStore.setState({
    rideState: 'idle',
    incomingRide: null,
    activeRide: null,
    completedRide: null,
    countdownSeconds: 0,
    isLoading: false,
    error: null,
    earnings: null,
    dailyEarnings: [],
    tripEarnings: [],
    bankAccount: null,
  });

beforeEach(() => {
  jest.clearAllMocks();
  resetStore();
});

describe('hydrateDriverRideState — mid-trip restart restore (P1-7 / D12)', () => {
  it('restores navigating_to_pickup state after cold start', async () => {
    const activeRide = makeActiveRide();
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(
      JSON.stringify({ rideState: 'navigating_to_pickup', activeRide }),
    );

    await useDriverStore.getState().hydrateDriverRideState();

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('navigating_to_pickup');
    expect(state.activeRide?.ride?.id).toBe('ride-drv-001');
  });

  it('restores arrived_at_pickup state after cold start', async () => {
    const activeRide = makeActiveRide();
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(
      JSON.stringify({ rideState: 'arrived_at_pickup', activeRide }),
    );

    await useDriverStore.getState().hydrateDriverRideState();

    expect(useDriverStore.getState().rideState).toBe('arrived_at_pickup');
    expect(useDriverStore.getState().activeRide?.ride?.id).toBe('ride-drv-001');
  });

  it('restores trip_in_progress state after cold start', async () => {
    const activeRide = makeActiveRide();
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(
      JSON.stringify({ rideState: 'trip_in_progress', activeRide }),
    );

    await useDriverStore.getState().hydrateDriverRideState();

    expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
    expect(useDriverStore.getState().activeRide?.ride?.id).toBe('ride-drv-001');
  });

  it('does not restore the idle terminal state', async () => {
    const activeRide = makeActiveRide();
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(
      JSON.stringify({ rideState: 'idle', activeRide }),
    );

    await useDriverStore.getState().hydrateDriverRideState();

    expect(useDriverStore.getState().rideState).toBe('idle');
    expect(useDriverStore.getState().activeRide).toBeNull();
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith(DRIVER_RIDE_KEY);
  });

  it('does not restore the trip_completed terminal state', async () => {
    const activeRide = makeActiveRide();
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(
      JSON.stringify({ rideState: 'trip_completed', activeRide }),
    );

    await useDriverStore.getState().hydrateDriverRideState();

    expect(useDriverStore.getState().rideState).toBe('idle');
    expect(useDriverStore.getState().activeRide).toBeNull();
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith(DRIVER_RIDE_KEY);
  });

  it('is a noop when AsyncStorage is empty', async () => {
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(null);

    await useDriverStore.getState().hydrateDriverRideState();

    expect(useDriverStore.getState().rideState).toBe('idle');
    expect(useDriverStore.getState().activeRide).toBeNull();
  });

  it('does not restore when activeRide is missing from storage', async () => {
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(
      JSON.stringify({ rideState: 'trip_in_progress', activeRide: null }),
    );

    await useDriverStore.getState().hydrateDriverRideState();

    // null activeRide is treated the same as terminal — no restore
    expect(useDriverStore.getState().activeRide).toBeNull();
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith(DRIVER_RIDE_KEY);
  });

  it('does not override an already-populated in-memory store', async () => {
    // fetchActiveRide ran before hydrateDriverRideState and already set state
    const liveRide = makeActiveRide({ id: 'ride-LIVE' });
    useDriverStore.setState({ activeRide: liveRide, rideState: 'trip_in_progress' });

    const cachedRide = makeActiveRide({ id: 'ride-OLD' });
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce(
      JSON.stringify({ rideState: 'navigating_to_pickup', activeRide: cachedRide }),
    );

    await useDriverStore.getState().hydrateDriverRideState();

    // In-memory live state must not be overwritten by stale cache
    expect(useDriverStore.getState().activeRide?.ride?.id).toBe('ride-LIVE');
    expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
  });

  it('clears storage on corrupted JSON and leaves store unchanged', async () => {
    (AsyncStorage.getItem as jest.Mock).mockResolvedValueOnce('INVALID_JSON{{{{');

    await useDriverStore.getState().hydrateDriverRideState();

    expect(useDriverStore.getState().activeRide).toBeNull();
    expect(AsyncStorage.removeItem).toHaveBeenCalledWith(DRIVER_RIDE_KEY);
  });
});
