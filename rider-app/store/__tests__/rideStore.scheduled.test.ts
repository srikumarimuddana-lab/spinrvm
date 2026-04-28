/**
 * R-P2-35: Store tests for scheduled rides, fetchAvailablePromos, and syncOfflineRequests.
 * Covers the async store actions that had no test coverage.
 */

jest.mock('react-native', () => ({
  NativeModules: { RNFBAppModule: {} },
  Alert: { alert: jest.fn() },
  Platform: { OS: 'ios' },
}));

jest.mock('../../utils/crashlytics', () => ({
  captureException: jest.fn(),
  setTag: jest.fn(),
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(), get: jest.fn(), put: jest.fn(), delete: jest.fn() },
}));

jest.mock('@shared/store/authStore', () => ({
  useAuthStore: { getState: jest.fn(() => ({ user: { id: 'user-abc' } })) },
}));

jest.mock('expo-router', () => ({
  router: { push: jest.fn(), replace: jest.fn() },
}));

import AsyncStorage from '@react-native-async-storage/async-storage';
import { useRideStore } from '../rideStore';
import api from '@shared/api/client';

const mockApi = api as jest.Mocked<typeof api>;
const mockStorage = AsyncStorage as jest.Mocked<typeof AsyncStorage>;

const makeScheduledRide = (id: string) => ({
  id,
  status: 'scheduled',
  pickup_address: '100 Queen St',
  dropoff_address: '200 King St',
  scheduled_time: new Date(Date.now() + 3600000).toISOString(),
});

beforeEach(() => {
  jest.clearAllMocks();
  mockStorage.getItem.mockResolvedValue(null);
  mockStorage.setItem.mockResolvedValue(undefined);
  useRideStore.setState({
    scheduledRides: [],
    availablePromos: [],
    appliedPromo: null,
    error: null,
  });
});

// ── fetchScheduledRides ──────────────────────────────────────────────────────

describe('fetchScheduledRides', () => {
  it('populates scheduledRides on success', async () => {
    const rides = [makeScheduledRide('r1'), makeScheduledRide('r2')];
    mockApi.get.mockResolvedValueOnce({ data: rides });
    await useRideStore.getState().fetchScheduledRides();
    expect(useRideStore.getState().scheduledRides).toEqual(rides);
    expect(mockApi.get).toHaveBeenCalledWith('/rides/scheduled');
  });

  it('leaves scheduledRides unchanged on API error', async () => {
    const existing = [makeScheduledRide('r-existing')];
    useRideStore.setState({ scheduledRides: existing });
    mockApi.get.mockRejectedValueOnce(new Error('Network error'));
    await useRideStore.getState().fetchScheduledRides();
    expect(useRideStore.getState().scheduledRides).toEqual(existing);
  });
});

// ── cancelScheduledRide ──────────────────────────────────────────────────────

describe('cancelScheduledRide', () => {
  it('removes the ride from scheduledRides on success', async () => {
    const rides = [makeScheduledRide('r1'), makeScheduledRide('r2')];
    useRideStore.setState({ scheduledRides: rides });
    mockApi.delete.mockResolvedValueOnce({ data: {} });
    await useRideStore.getState().cancelScheduledRide('r1');
    const state = useRideStore.getState();
    expect(state.scheduledRides).toHaveLength(1);
    expect(state.scheduledRides[0].id).toBe('r2');
    expect(mockApi.delete).toHaveBeenCalledWith('/rides/scheduled/r1');
  });

  it('sets error state when API call fails', async () => {
    useRideStore.setState({ scheduledRides: [makeScheduledRide('r1')] });
    mockApi.delete.mockRejectedValueOnce(new Error('Server error'));
    await useRideStore.getState().cancelScheduledRide('r1');
    expect(useRideStore.getState().error).toBe('Server error');
    expect(useRideStore.getState().scheduledRides).toHaveLength(1);
  });
});

// ── fetchAvailablePromos ─────────────────────────────────────────────────────

describe('fetchAvailablePromos', () => {
  it('sets availablePromos and auto-applies first promo', async () => {
    const promos = [
      { id: 'p1', code: 'SAVE10', discount: 10 },
      { id: 'p2', code: 'SAVE5', discount: 5 },
    ];
    mockApi.get.mockResolvedValueOnce({ data: promos });
    await useRideStore.getState().fetchAvailablePromos(15);
    const state = useRideStore.getState();
    expect(state.availablePromos).toEqual(promos);
    expect(state.appliedPromo).toEqual(promos[0]);
    expect(mockApi.get).toHaveBeenCalledWith('/promo/available?ride_fare=15');
  });

  it('does not overwrite an already-applied promo', async () => {
    const existing = { id: 'p-existing', code: 'EXISTING', discount: 20 };
    useRideStore.setState({ appliedPromo: existing });
    mockApi.get.mockResolvedValueOnce({ data: [{ id: 'p-new', code: 'NEW', discount: 5 }] });
    await useRideStore.getState().fetchAvailablePromos(10);
    expect(useRideStore.getState().appliedPromo).toEqual(existing);
  });

  it('sets availablePromos to empty array on API error', async () => {
    mockApi.get.mockRejectedValueOnce(new Error('Network error'));
    await useRideStore.getState().fetchAvailablePromos();
    expect(useRideStore.getState().availablePromos).toEqual([]);
  });
});

// ── syncOfflineRequests ──────────────────────────────────────────────────────

describe('syncOfflineRequests', () => {
  it('does nothing when queue is empty', async () => {
    mockStorage.getItem.mockResolvedValueOnce(null);
    await useRideStore.getState().syncOfflineRequests();
    expect(mockApi.post).not.toHaveBeenCalled();
  });

  it('replays cancel_ride requests and removes them from queue', async () => {
    const queue = [{ id: 'q1', type: 'cancel_ride', rideId: 'r1' }];
    mockStorage.getItem.mockResolvedValueOnce(JSON.stringify(queue));
    mockApi.post.mockResolvedValueOnce({ data: {} });
    await useRideStore.getState().syncOfflineRequests();
    expect(mockApi.post).toHaveBeenCalledWith('/rides/r1/cancel');
    const saved = JSON.parse(mockStorage.setItem.mock.calls[0][1]);
    expect(saved).toHaveLength(0);
  });

  it('replays rate_ride requests', async () => {
    const queue = [{ id: 'q2', type: 'rate_ride', rideId: 'r2', data: { rating: 5 } }];
    mockStorage.getItem.mockResolvedValueOnce(JSON.stringify(queue));
    mockApi.post.mockResolvedValueOnce({ data: {} });
    await useRideStore.getState().syncOfflineRequests();
    expect(mockApi.post).toHaveBeenCalledWith('/rides/r2/rate', { rating: 5 });
  });

  it('increments retryCount on failure and drops after 3 attempts', async () => {
    const queue = [{ id: 'q3', type: 'cancel_ride', rideId: 'r3', retryCount: 2 }];
    mockStorage.getItem.mockResolvedValueOnce(JSON.stringify(queue));
    mockApi.post.mockRejectedValueOnce(new Error('Server error'));
    await useRideStore.getState().syncOfflineRequests();
    const saved = JSON.parse(mockStorage.setItem.mock.calls[0][1]);
    expect(saved).toHaveLength(0);
  });
});
