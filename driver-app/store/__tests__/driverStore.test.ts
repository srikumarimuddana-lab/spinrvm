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
  return {
    __esModule: true,
    default: mockClient,
    // Same contract as the real helper: backend detail wins, else fallback.
    getApiErrorMessage: jest.fn(
      (err: any, fallback: string) => err?.response?.data?.detail || fallback,
    ),
  };
});

// Mock expo-router
jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

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

import { useDriverStore } from '../driverStore';
import api from '@shared/api/client';
import { tripLocationRecorder } from '../../utils/tripLocationRecorder';

const mockApi = api as jest.Mocked<typeof api>;
const mockTripLocationRecorder = tripLocationRecorder as jest.Mocked<typeof tripLocationRecorder>;

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
  mockTripLocationRecorder.startRide.mockResolvedValue({
    recording_session_id: 'session-123',
    ride_id: 'ride-123',
    opened_at: '2026-07-21T12:00:00.000Z',
    closed_at: null,
  });
  mockTripLocationRecorder.captureCompletionFix.mockResolvedValue({
    point: {
      ride_id: 'ride-123',
      recording_session_id: 'session-123',
      sequence_number: 9,
      captured_at: '2026-07-17T22:45:00.000Z',
      monotonic_ms: 1,
      lat: 52.1,
      lng: -106.6,
      accuracy: 5,
      speed: 10,
      heading: 90,
      altitude: null,
      source: 'completion',
      mocked: false,
      is_completion_fix: true,
    },
    pendingCount: 3,
  });
  mockTripLocationRecorder.applyAcknowledgement.mockResolvedValue(1);
  mockTripLocationRecorder.closeRide.mockResolvedValue(undefined);
  mockTripLocationRecorder.flushPendingWithTimeout.mockResolvedValue({
    uploaded_points: 0,
    acknowledged_points: 0,
    skipped: true,
    timedOut: false,
  });
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
  fare: '12.50',
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
      response: { status: 500, data: { detail: 'Internal server error' } },
    } as any);

    await act(async () => {
      await useDriverStore.getState().acceptRide('ride-123');
    });

    expect(useDriverStore.getState().error).toBe('Internal server error');
  });

  test('acceptRide 409 proceeds as success when this driver actually owns the ride', async () => {
    // Regression: a duplicate accept request (double-tap / retry) 409s with
    // "Ride already accepted by another driver" even though THIS driver won.
    // The store must verify ownership and keep the ride — no misleading toast.
    useDriverStore.setState({ rideState: 'ride_offered', incomingRide: makeMockRide() });

    mockApi.post.mockRejectedValueOnce({
      response: { status: 409, data: { detail: 'Ride already accepted by another driver' } },
    } as any);
    // Ownership verification: /drivers/rides/active returns OUR ride, accepted.
    mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('driver_accepted') as any);

    await act(async () => {
      await useDriverStore.getState().acceptRide('ride-123');
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('navigating_to_pickup');
    expect(state.activeRide?.ride?.id).toBe('ride-123');
    expect(state.error).toBeNull();
  });

  test('acceptRide 409 resets to idle with taken message when another driver won', async () => {
    useDriverStore.setState({ rideState: 'ride_offered', incomingRide: makeMockRide() });

    mockApi.post.mockRejectedValueOnce({
      response: { status: 409, data: { detail: 'Ride already accepted by another driver' } },
    } as any);
    // Ownership verification: no active ride for this driver — genuinely lost.
    mockApi.get.mockResolvedValueOnce({ data: null } as any);

    await act(async () => {
      await useDriverStore.getState().acceptRide('ride-123');
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('idle');
    expect(state.incomingRide).toBeNull();
    expect(state.error).toMatch(/already taken by another driver/i);
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

  // Gap #13: a pre-accept decline previously had no way to carry a reason
  // at all, so trust & safety had no way to detect a driver refusing a
  // service animal. declineRide now accepts an optional reason.
  test('declineRide posts an optional reason (e.g. service_animal) in the body', async () => {
    useDriverStore.setState({ rideState: 'ride_offered', incomingRide: makeMockRide() });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().declineRide('ride-123', 'service_animal');
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('idle');
    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/decline', { reason: 'service_animal' });
  });

  test('declineRide with an empty/whitespace-only reason falls back to the no-body request', async () => {
    useDriverStore.setState({ rideState: 'ride_offered', incomingRide: makeMockRide() });

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().declineRide('ride-123', '   ');
    });

    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/decline');
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
          dropoff_lat: 52.2,
          dropoff_lng: -106.8,
          total_fare: '12.50',
          distance_km: 5.2,
          duration_minutes: 12,
          rider_id: 'user-1',
          created_at: '2024-01-01',
        },
        rider: { id: 'user-1' },
        vehicle_type: { id: 'vt-1', name: 'Standard' },
      },
    } as any);

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
          dropoff_lat: 52.2,
          dropoff_lng: -106.8,
          total_fare: '12.50',
          distance_km: 5.2,
          duration_minutes: 12,
          rider_id: 'user-1',
          created_at: '2024-01-01',
        },
        rider: { id: 'user-1' },
        vehicle_type: { id: 'vt-1', name: 'Standard' },
      },
    } as any);

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
    expect(mockTripLocationRecorder.startRide).toHaveBeenCalledWith('ride-123');
    expect(mockTripLocationRecorder.startRide.mock.invocationCallOrder[0]).toBeLessThan(
      mockApi.get.mock.invocationCallOrder[0],
    );
  });

  test('startRide initializes durable recording before active ride hydration', async () => {
    useDriverStore.setState({ rideState: 'arrived_at_pickup' });
    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);
    mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('in_progress') as any);

    await act(async () => {
      await useDriverStore.getState().startRide('ride-123');
    });

    expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
    expect(mockTripLocationRecorder.startRide).toHaveBeenCalledWith('ride-123');
    expect(mockTripLocationRecorder.startRide.mock.invocationCallOrder[0]).toBeLessThan(
      mockApi.get.mock.invocationCallOrder[0],
    );
  });

  test('keeps the ride in progress when durable recorder startup needs dashboard retry', async () => {
    useDriverStore.setState({ rideState: 'arrived_at_pickup' });
    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);
    mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('in_progress') as any);
    mockTripLocationRecorder.startRide.mockRejectedValueOnce(new Error('database is locked'));

    await act(async () => {
      await useDriverStore.getState().startRide('ride-123');
    });

    expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
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
    expect(mockTripLocationRecorder.captureCompletionFix).toHaveBeenCalledWith('ride-123');
    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/complete', expect.objectContaining({
      final_session_id: 'session-123',
      final_sequence_number: 9,
      pending_outbox_count: 3,
      off_route_confirmation: null,
      completion_fix: expect.objectContaining({ is_completion_fix: true }),
    }));
  });

  test('completeRide drains the outbox before capturing the completion fix and posting', async () => {
    const callOrder: string[] = [];
    mockTripLocationRecorder.flushPendingWithTimeout.mockImplementation(async () => {
      callOrder.push('flush');
      return { uploaded_points: 5, acknowledged_points: 5, skipped: false, timedOut: false };
    });
    mockTripLocationRecorder.captureCompletionFix.mockImplementation(async () => {
      callOrder.push('capture');
      return { point: null, pendingCount: 0 };
    });
    useDriverStore.setState({ rideState: 'trip_in_progress' });
    mockApi.post.mockImplementation(async () => {
      callOrder.push('post');
      return { data: { ride_id: 'ride-123', status: 'completed' }, status: 200 } as any;
    });

    await act(async () => {
      await useDriverStore.getState().completeRide('ride-123');
    });

    expect(callOrder).toEqual(['flush', 'capture', 'post']);
    expect(mockTripLocationRecorder.flushPendingWithTimeout).toHaveBeenCalledWith(
      expect.anything(),
      8_000,
    );
  });

  test('completeRide still completes when the pre-flush times out or fails', async () => {
    useDriverStore.setState({ rideState: 'trip_in_progress' });
    mockTripLocationRecorder.flushPendingWithTimeout.mockRejectedValueOnce(new Error('network down'));
    mockApi.post.mockResolvedValueOnce({ data: { ride_id: 'ride-123', status: 'completed' }, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().completeRide('ride-123');
    });

    expect(useDriverStore.getState().rideState).toBe('trip_completed');
    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/complete', expect.anything());
  });

  test('completeRide applies the server acknowledgement before closing the local recording session', async () => {
    const completedData = {
      ride_id: 'ride-123',
      status: 'completed',
      location_ack: {
        recording_session_id: 'session-123',
        acked_through: 9,
        rejected: [],
      },
    };
    useDriverStore.setState({ rideState: 'trip_in_progress' });
    mockApi.post.mockResolvedValueOnce({ data: completedData, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().completeRide('ride-123');
    });

    expect(mockTripLocationRecorder.applyAcknowledgement).toHaveBeenCalledWith(completedData.location_ack);
    expect(mockTripLocationRecorder.closeRide).toHaveBeenCalledWith('ride-123');
  });

  test('completeRide returns a confirmation request instead of treating an off-route 409 as completed', async () => {
    useDriverStore.setState({ rideState: 'trip_in_progress' });
    mockApi.post.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: {
            code: 'completion_confirmation_required',
            distance_band: 'off_route',
          },
        },
      },
    } as any);

    let result: unknown;
    await act(async () => {
      result = await useDriverStore.getState().completeRide('ride-123');
    });

    expect(result).toEqual({ confirmationRequired: true, distanceBand: 'off_route' });
    expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
    expect(useDriverStore.getState().error).toBeNull();
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  test('resetRideState returns everything to idle', () => {
    useDriverStore.setState({
      rideState: 'trip_completed',
      completedRide: { fare: 12.5 } as any,
      activeRide: {
        ride: { id: 'ride-1', status: 'completed', pickup_address: '1 Main', dropoff_address: '2 Main', pickup_lat: 0, pickup_lng: 0, dropoff_lat: 0, dropoff_lng: 0, total_fare: '12.50', distance_km: 5, duration_minutes: 10, rider_id: 'r-1', created_at: '2024-01-01' },
        rider: { id: 'r-1' },
        vehicle_type: { id: 'vt-1', name: 'Standard' },
      },
      incomingRide: makeMockRide() as any,
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

  test('fetchActiveRide resumes ride_offered when backend status is driver_assigned', async () => {
    mockApi.get.mockResolvedValueOnce({
      data: {
        ride: {
          id: 'ride-999',
          status: 'driver_assigned',
          pickup_address: '1 Pickup St',
          dropoff_address: '2 Dropoff Ave',
          pickup_lat: 52.1,
          pickup_lng: -106.6,
          dropoff_lat: 52.2,
          dropoff_lng: -106.7,
          fare: 15.75,
          distance_km: 4.1,
          duration_minutes: 9,
        },
        rider: { id: 'user-1', first_name: 'Bob', rating: 4.9 },
        vehicle_type: null,
      },
    } as any);

    await act(async () => {
      await useDriverStore.getState().fetchActiveRide();
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('ride_offered');
    expect(state.incomingRide).not.toBeNull();
    expect(state.incomingRide?.ride_id).toBe('ride-999');
    expect(state.incomingRide?.rider_name).toBe('Bob');
    expect(state.incomingRide?.rider_rating).toBe(4.9);
    expect(state.incomingRide?.fare).toBe(15.75);
    expect(state.countdownSeconds).toBeGreaterThan(0);
    expect(state.activeRide).not.toBeNull();
  });

  test('fetchActiveRide transitions to navigating_to_pickup when status is driver_accepted', async () => {
    // Pre-seed a stale incomingRide — the fetch should clear it.
    useDriverStore.setState({
      rideState: 'ride_offered',
      incomingRide: makeMockRide(),
      countdownSeconds: 10,
    });
    mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('driver_accepted') as any);

    await act(async () => {
      await useDriverStore.getState().fetchActiveRide();
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('navigating_to_pickup');
    expect(state.incomingRide).toBeNull();
    expect(state.countdownSeconds).toBe(0);
  });

  test('fetchActiveRide overrides a stale ride_offered when the server shows a later forward status', async () => {
    // Issue #5: a live local offer must not shadow an authoritative forward
    // transition. Here the server reports driver_arrived while the client is
    // still stuck on the offer panel — the server wins.
    useDriverStore.setState({
      rideState: 'ride_offered',
      incomingRide: makeMockRide(),
      countdownSeconds: 12,
    });
    mockApi.get.mockResolvedValueOnce(makeActiveRideResponse('driver_arrived') as any);

    await act(async () => {
      await useDriverStore.getState().fetchActiveRide();
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('arrived_at_pickup');
    expect(state.incomingRide).toBeNull();
  });

  test('cancelRide resets to idle and clears active/incoming ride', async () => {
    useDriverStore.setState({
      rideState: 'navigating_to_pickup',
      activeRide: {
        ride: { id: 'ride-123', status: 'driver_accepted', pickup_address: '1 Main', dropoff_address: '2 Main', pickup_lat: 0, pickup_lng: 0, dropoff_lat: 0, dropoff_lng: 0, total_fare: '12.50', distance_km: 5, duration_minutes: 10, rider_id: 'r-1', created_at: '2024-01-01' },
        rider: { id: 'r-1' },
        vehicle_type: { id: 'vt-1', name: 'Standard' },
      },
    } as any);

    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().cancelRide('ride-123', 'Driver unavailable');
    });

    const state = useDriverStore.getState();
    expect(state.rideState).toBe('idle');
    expect(state.activeRide).toBeNull();
    expect(state.incomingRide).toBeNull();
    expect(mockApi.post).toHaveBeenCalledWith(
      expect.stringContaining('ride-123/cancel'),
      { reason: 'Driver unavailable' }
    );
  });
});

describe('driverStore — earnings, ride history, bank account, rating', () => {
  test('fetchEarnings requests the given period and stores the result', async () => {
    const mockEarnings = { period: 'day', total_earnings: 150, total_rides: 8 } as any;
    mockApi.get.mockResolvedValueOnce({ data: mockEarnings, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().fetchEarnings('day');
    });

    expect(mockApi.get).toHaveBeenCalledWith('/drivers/earnings?period=day');
    expect(useDriverStore.getState().earnings).toEqual(mockEarnings);
  });

  test('fetchRideHistory requests the given page and stores rides + total', async () => {
    const mockHistory = { rides: [{ id: 'ride-1' }], total: 1 } as any;
    mockApi.get.mockResolvedValueOnce({ data: mockHistory, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().fetchRideHistory(10, 0);
    });

    expect(mockApi.get).toHaveBeenCalledWith('/drivers/rides/history?limit=10&offset=0');
    expect(useDriverStore.getState().rideHistory).toHaveLength(1);
    expect(useDriverStore.getState().historyTotal).toBe(1);
  });

  test('fetchBankAccount stores hasBankAccount + bankAccount from the response', async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { has_bank_account: true, bank_account: { bank_name: 'TD' } },
      status: 200,
    } as any);

    await act(async () => {
      await useDriverStore.getState().fetchBankAccount();
    });

    const state = useDriverStore.getState();
    expect(state.hasBankAccount).toBe(true);
    expect(state.bankAccount?.bank_name).toBe('TD');
  });

  test('deleteBankAccount clears the stored account and resolves true', async () => {
    useDriverStore.setState({ hasBankAccount: true, bankAccount: { bank_name: 'TD' } as any });
    mockApi.delete.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    let result: boolean;
    await act(async () => {
      result = await useDriverStore.getState().deleteBankAccount();
    });

    expect(result!).toBe(true);
    expect(mockApi.delete).toHaveBeenCalledWith('/drivers/bank-account');
    const state = useDriverStore.getState();
    expect(state.hasBankAccount).toBe(false);
    expect(state.bankAccount).toBeNull();
  });

  test('deleteBankAccount sets error and resolves false on API failure', async () => {
    mockApi.delete.mockRejectedValueOnce({
      response: { status: 500, data: { detail: 'Bank service unavailable' } },
    } as any);

    let result: boolean;
    await act(async () => {
      result = await useDriverStore.getState().deleteBankAccount();
    });

    expect(result!).toBe(false);
    expect(useDriverStore.getState().error).toBe('Bank service unavailable');
  });

  test('rateRider posts the rating and comment for the given ride', async () => {
    mockApi.post.mockResolvedValueOnce({ data: {}, status: 200 } as any);

    await act(async () => {
      await useDriverStore.getState().rateRider('ride-123', 5, 'Great passenger');
    });

    expect(mockApi.post).toHaveBeenCalledWith('/drivers/rides/ride-123/rate-rider', {
      rating: 5,
      comment: 'Great passenger',
    });
  });

  test('rateRider sets error on API failure', async () => {
    mockApi.post.mockRejectedValueOnce({
      response: { status: 500, data: { detail: 'Rating service unavailable' } },
    } as any);

    await act(async () => {
      await useDriverStore.getState().rateRider('ride-123', 5);
    });

    expect(useDriverStore.getState().error).toBe('Rating service unavailable');
  });
});
