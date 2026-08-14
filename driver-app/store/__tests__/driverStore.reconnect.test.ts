/**
 * P1-6: WebSocket reconnect state preservation — driver side.
 *
 * The hook change lives in `useDriverDashboard.ts` (a `justReconnectedRef`
 * flag flipped in `ws.onopen`, consumed on the first valid auth-confirmed
 * message to call `fetchActiveRide()`). That flag is an internal detail;
 * the observable contract is that `fetchActiveRide()` reconciles the
 * store with the server's authoritative state after a disconnect window.
 *
 * These tests pin that contract: after a disconnect-during-lifecycle,
 * calling fetchActiveRide() from the reconnect path restores the correct
 * in-memory state (cancel, status advance, ride cleared).
 */

import { useDriverStore } from '../driverStore';
import api from '@shared/api/client';

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { rideOffer: { countdownSeconds: 15 } },
}));

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

jest.mock('expo-router', () => ({ router: { push: jest.fn(), replace: jest.fn() } }));

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

const mockApi = api as jest.Mocked<typeof api>;

const resetStore = () =>
  useDriverStore.setState({
    rideState: 'idle',
    incomingRide: null,
    activeRide: null,
    completedRide: null,
    countdownSeconds: 0,
    isLoading: false,
    error: null,
  });

beforeEach(() => {
  jest.clearAllMocks();
  resetStore();
});

describe('driverStore reconnect sync (P1-6)', () => {
  it('advances state when the server shows the ride progressed mid-disconnect', async () => {
    // Driver was in navigating_to_pickup when the socket dropped.
    useDriverStore.setState({
      rideState: 'navigating_to_pickup',
      activeRide: { ride: { id: 'ride-1', status: 'driver_accepted' } } as any,
    });

    // Server side: the rider confirmed arrival (status → driver_arrived) while
    // the socket was down. The reconnect handler calls fetchActiveRide().
    mockApi.get.mockResolvedValueOnce({
      data: {
        ride: {
          id: 'ride-1',
          status: 'driver_arrived',
          pickup_address: '1 Main',
          dropoff_address: '2 Main',
          pickup_lat: 0, pickup_lng: 0,
          dropoff_lat: 0, dropoff_lng: 0,
        },
        rider: null,
      },
      status: 200,
    });

    await useDriverStore.getState().fetchActiveRide();

    expect(useDriverStore.getState().rideState).toBe('arrived_at_pickup');
    expect(mockApi.get).toHaveBeenCalledWith('/drivers/rides/active');
  });

  it('clears local state when the server says the ride was cancelled mid-disconnect', async () => {
    // Driver had an active ride; rider cancelled while the socket was down.
    useDriverStore.setState({
      rideState: 'navigating_to_pickup',
      activeRide: { ride: { id: 'ride-1', status: 'driver_accepted' } } as any,
    });

    // Server returns nothing — the cancelled ride is no longer "active".
    mockApi.get.mockResolvedValueOnce({ data: {}, status: 200 });

    await useDriverStore.getState().fetchActiveRide();

    expect(useDriverStore.getState().rideState).toBe('idle');
    expect(useDriverStore.getState().activeRide).toBeNull();
  });

  it('falls back to idle on fetchActiveRide network error (stale cache trumped by safe default)', async () => {
    useDriverStore.setState({
      rideState: 'arrived_at_pickup',
      activeRide: { ride: { id: 'ride-1', status: 'driver_arrived' } } as any,
    });

    mockApi.get.mockRejectedValueOnce(new Error('network'));

    await useDriverStore.getState().fetchActiveRide();

    // Fetch failed — store resets to idle rather than leaving stale state
    // hidden behind the panel.
    expect(useDriverStore.getState().rideState).toBe('idle');
    expect(useDriverStore.getState().activeRide).toBeNull();
  });

  it('re-hydrates ride_offered state when the server shows a fresh offer', async () => {
    // Driver was idle; while socket was down, a new offer was dispatched.
    useDriverStore.setState({ rideState: 'idle', activeRide: null });

    mockApi.get.mockResolvedValueOnce({
      data: {
        ride: {
          id: 'ride-2',
          status: 'driver_assigned',
          pickup_address: '1 Main', dropoff_address: '2 Main',
          pickup_lat: 0, pickup_lng: 0, dropoff_lat: 0, dropoff_lng: 0,
          driver_earnings: 10, distance_km: 3, duration_minutes: 8,
        },
        rider: { first_name: 'Sam', rating: 4.8 },
      },
      status: 200,
    });

    await useDriverStore.getState().fetchActiveRide();

    expect(useDriverStore.getState().rideState).toBe('ride_offered');
    expect(useDriverStore.getState().incomingRide?.ride_id).toBe('ride-2');
    expect(useDriverStore.getState().incomingRide?.rider_name).toBe('Sam');
  });
});
