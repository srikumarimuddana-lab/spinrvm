/**
 * driverStore state machine tests (TST-002)
 * Tests the ride lifecycle transitions without hitting the network.
 */
import { act } from '@testing-library/react-native';

// Mock SpinrConfig before importing the store (imported at module level in driverStore)
jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: {
    rideOffer: { countdownSeconds: 15 },
  },
}));

// Mock the API client before importing the store
jest.mock('@shared/api/client', () => {
  const mockClient = {
    post: jest.fn(),
    get: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
    put: jest.fn(),
  };
  return { __esModule: true, default: mockClient };
});

// Mock expo-router
jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

import { useDriverStore } from '../driverStore';
import api from '@shared/api/client';

const mockApi = api as jest.Mocked<typeof api>;

/** Reset store to idle baseline before each test */
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
    driverBalance: null,
    payoutHistory: [],
    hasBankAccount: false,
    t4aSummaries: [],
    availableYears: [],
    selectedYear: null,
    rideHistory: [],
    historyTotal: 0,
  });

beforeEach(() => {
  jest.clearAllMocks();
  resetStore();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const makeMockRide = (overrides: Record<string, unknown> = {}) => ({
  ride_id: 'ride-123',
  pickup_address: '123 Main St',
  dropoff_address: '456 Oak Ave',
  pickup_lat: 52.1332,
  pickup_lng: -106.6700,
  dropoff_lat: 52.2,
  dropoff_lng: -106.8,
  fare: 12.5,
  distance_km: 5.2,
  duration_minutes: 12,
  rider_name: 'Alice',
  rider_rating: 4.8,
  ...overrides,
});

const makeActiveRideResponse = (rideStatus = 'driver_accepted') => ({
  data: {
    ride: {
      id: 'ride-123',
      status: rideStatus,
      pickup_address: '123 Main St',
      dropoff_address: '456 Oak Ave',
      pickup_lat: 52.1332,
      pickup_lng: -106.6700,
      dropoff_lat: 52.2,
      dropoff_lng: -106.8,
    },
    rider: { id: 'user-1', first_name: 'Alice' },
  },
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('driverStore — ride state machine', () => {
  test('initial state is idle with null ride fields', () => {
    expect(useDriverStore.getState().rideState).toBe('idle');
    expect(useDriverStore.getState().incomingRide).toBeNull();
    expect(useDriverStore.getState().activeRide).toBeNull();
    expect(useDriverStore.getState().completedRide).toBeNull();
  });

  test('setIncomingRide transitions to ride_offered and stores the ride', () => {
    const mockRide = makeMockRide();

    act(() => {
      useDriverStore.getState().setIncomingRide(mockRide);
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('ride_offered');
    expect(state.incomingRide).toEqual(mockRide);
    expect(state.countdownSeconds).toBe(15);
  });

  test('setIncomingRide(null) returns to idle', () => {
    act(() => {
      useDriverStore.getState().setIncomingRide(makeMockRide());
    });
    act(() => {
      useDriverStore.getState().setIncomingRide(null);
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('idle');
    expect(state.incomingRide).toBeNull();
    expect(state.countdownSeconds).toBe(0);
  });

  test('acceptRide transitions to navigating_to_pickup and calls API', async () => {
    useDriverStore.setState({ rideState: 'ride_offered', incomingRide: makeMockRide() });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);
    // fetchActiveRide GET
    mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('driver_accepted') as any);

    await act(async () => {
      await useDriverStore.getState().acceptRide('ride-123');
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('navigating_to_pickup');
    expect(state.incomingRide).toBeNull();
    expect(state.countdownSeconds).toBe(0);
    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/accept');
    expect(mockApi.get).toHaveBeenCalledWith('/drivers/rides/active');
  });

  test('acceptRide sets error when API fails', async () => {
    useDriverStore.setState({ rideState: 'ride_offered', incomingRide: makeMockRide() });

    mockApi.post.mockRejectedValueOnce({
      response: { status: 409, data: { detail: 'Ride no longer available' } },
    } as any);

    await act(async () => {
      await useDriverStore.getState().acceptRide('ride-123');
    });

    expect(useDriverStore.getState().error).toBe('Ride no longer available');
  });

  test('declineRide returns to idle and calls decline endpoint', async () => {
    useDriverStore.setState({ rideState: 'ride_offered', incomingRide: makeMockRide() });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().declineRide('ride-123');
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('idle');
    expect(state.incomingRide).toBeNull();
    expect(state.countdownSeconds).toBe(0);
    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/decline');
  });

  test('declineRide still returns to idle even if API call fails', async () => {
    useDriverStore.setState({ rideState: 'ride_offered', incomingRide: makeMockRide() });

    mockApi.post.mockRejectedValueOnce(new Error('Network error') as any);

    await act(async () => {
      await useDriverStore.getState().declineRide('ride-123');
    });

    // declineRide swallows the error and still resets state
    expect(useDriverStore.getState().rideState).toBe('idle');
    expect(useDriverStore.getState().incomingRide).toBeNull();
  });

  test('arriveAtPickup rejects when driver is >100m from pickup', async () => {
    useDriverStore.setState({
      rideState: 'navigating_to_pickup',
      activeRide: {
        ride: {
          id: 'ride-123',
          status: 'driver_accepted',
          pickup_lat: 52.1332,
          pickup_lng: -106.6700,
          pickup_address: '123 Main St',
          dropoff_address: '456 Oak Ave',
        },
        rider: { id: 'user-1', first_name: 'Alice' },
        vehicle_type: null,
      },
    });

    // Driver is ~14.8 km away — well outside 100m radius
    const result = await act(async () =>
      useDriverStore.getState().arriveAtPickup('ride-123', 52.0000, -106.6700)
    );

    expect(result.success).toBe(false);
    expect(useDriverStore.getState().rideState).toBe('navigating_to_pickup');
    expect(useDriverStore.getState().error).toContain('within');
  });

  test('arriveAtPickup succeeds when driver is within 100m', async () => {
    useDriverStore.setState({
      rideState: 'navigating_to_pickup',
      activeRide: {
        ride: {
          id: 'ride-123',
          status: 'driver_accepted',
          pickup_lat: 52.1332,
          pickup_lng: -106.6700,
          pickup_address: '123 Main St',
          dropoff_address: '456 Oak Ave',
        },
        rider: { id: 'user-1', first_name: 'Alice' },
        vehicle_type: null,
      },
    });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);
    // fetchActiveRide called after arrive
    mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('driver_arrived') as any);

    // 52.1336, -106.6700 is ~44m north of pickup — inside 100m
    const result = await act(async () =>
      useDriverStore.getState().arriveAtPickup('ride-123', 52.1336, -106.6700)
    );

    expect(result.success).toBe(true);
    expect(useDriverStore.getState().rideState).toBe('arrived_at_pickup');
    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/arrive');
  });

  test('verifyOTP transitions to trip_in_progress on success', async () => {
    useDriverStore.setState({ rideState: 'arrived_at_pickup' });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);
    mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('in_progress') as any);

    let result: boolean;
    await act(async () => {
      result = await useDriverStore.getState().verifyOTP('ride-123', '4321');
    });

    expect(result!).toBe(true);
    expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/verify-otp', { otp: '4321' });
  });

  test('verifyOTP returns false and sets error on wrong OTP', async () => {
    useDriverStore.setState({ rideState: 'arrived_at_pickup' });

    mockApi.post.mockRejectedValueOnce({
      response: { status: 400, data: { detail: 'Invalid OTP' } },
    } as any);

    let result: boolean;
    await act(async () => {
      result = await useDriverStore.getState().verifyOTP('ride-123', '0000');
    });

    expect(result!).toBe(false);
    expect(useDriverStore.getState().error).toBe('Invalid OTP');
    expect(useDriverStore.getState().rideState).toBe('arrived_at_pickup');
  });

  test('completeRide transitions to trip_completed and stores completedRide', async () => {
    const completedData = { ride_id: 'ride-123', fare: 12.5, status: 'completed' };
    useDriverStore.setState({ rideState: 'trip_in_progress' });

    mockApi.post.mockResolvedValueOnce({ data: completedData, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().completeRide('ride-123');
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('trip_completed');
    expect(state.completedRide).toEqual(completedData);
    expect(state.activeRide).toBeNull();
    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/complete');
  });

  test('resetRideState returns everything to idle', () => {
    useDriverStore.setState({
      rideState: 'trip_completed',
      completedRide: { fare: 12.5 } as any,
      activeRide: { ride: {}, rider: {}, vehicle_type: null },
      incomingRide: makeMockRide(),
      countdownSeconds: 5,
      error: 'some error',
    });

    act(() => {
      useDriverStore.getState().resetRideState();
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('idle');
    expect(state.completedRide).toBeNull();
    expect(state.activeRide).toBeNull();
    expect(state.incomingRide).toBeNull();
    expect(state.countdownSeconds).toBe(0);
    expect(state.error).toBeNull();
  });

  test('cancelRide resets to idle and clears active/incoming ride', async () => {
    useDriverStore.setState({
      rideState: 'navigating_to_pickup',
      activeRide: { ride: { id: 'ride-123' }, rider: {}, vehicle_type: null },
    });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().cancelRide('ride-123', 'Driver unavailable');
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('idle');
    expect(state.activeRide).toBeNull();
    expect(state.incomingRide).toBeNull();
    expect(mockApi.post).toHaveBeenCalledWith(
      expect.stringContaining('ride-123/cancel')
    );
  });
});
