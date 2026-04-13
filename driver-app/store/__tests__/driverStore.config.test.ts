// Mock SpinrConfig before importing the store
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

const api = jest.requireMock('@shared/api/client').default;

describe('driverStore — config + race handling', () => {
  beforeEach(() => {
    useDriverStore.setState({
      rideState: 'idle',
      incomingRide: null,
      activeRide: null,
      completedRide: null,
      countdownSeconds: 0,
      configuredCountdownSeconds: 15,
      configuredPickupRadiusMeters: 100,
      isLoading: false,
      error: null,
    });
    jest.clearAllMocks();
  });

  describe('applyDriverConfig', () => {
    it('should set countdown and radius from server config', () => {
      useDriverStore.getState().applyDriverConfig({
        ride_offer_timeout_seconds: 25,
        pickup_radius_meters: 200,
      });
      const state = useDriverStore.getState();
      expect(state.configuredCountdownSeconds).toBe(25);
      expect(state.configuredPickupRadiusMeters).toBe(200);
    });

    it('should ignore zero values', () => {
      useDriverStore.getState().applyDriverConfig({
        ride_offer_timeout_seconds: 0,
        pickup_radius_meters: 0,
      });
      const state = useDriverStore.getState();
      expect(state.configuredCountdownSeconds).toBe(15); // unchanged
      expect(state.configuredPickupRadiusMeters).toBe(100); // unchanged
    });

    it('should ignore negative values', () => {
      useDriverStore.getState().applyDriverConfig({
        ride_offer_timeout_seconds: -5,
        pickup_radius_meters: -100,
      });
      const state = useDriverStore.getState();
      expect(state.configuredCountdownSeconds).toBe(15);
      expect(state.configuredPickupRadiusMeters).toBe(100);
    });

    it('should apply partial updates (only countdown)', () => {
      useDriverStore.getState().applyDriverConfig({
        ride_offer_timeout_seconds: 30,
      });
      const state = useDriverStore.getState();
      expect(state.configuredCountdownSeconds).toBe(30);
      expect(state.configuredPickupRadiusMeters).toBe(100); // unchanged
    });
  });

  describe('acceptRide — race condition handling', () => {
    it('should reset to idle on 400 with "already" in detail', async () => {
      const error = new Error('Ride already accepted');
      (error as any).response = {
        status: 400,
        data: { detail: 'Ride already accepted by another driver' },
      };
      api.post.mockRejectedValueOnce(error);

      useDriverStore.getState().setIncomingRide({
        ride_id: 'ride-1',
        pickup_address: '123 Main',
        dropoff_address: '456 Elm',
        pickup_lat: 50,
        pickup_lng: -104,
        dropoff_lat: 51,
        dropoff_lng: -105,
        fare: 15,
      });

      await useDriverStore.getState().acceptRide('ride-1');

      const state = useDriverStore.getState();
      expect(state.rideState).toBe('idle');
      expect(state.incomingRide).toBeNull();
      expect(state.countdownSeconds).toBe(0);
      expect(state.error).toContain('already taken');
    });

    it('should reset to idle on 404 (ride gone)', async () => {
      const error = new Error('Not found');
      (error as any).response = {
        status: 404,
        data: { detail: 'Ride not found' },
      };
      api.post.mockRejectedValueOnce(error);

      await useDriverStore.getState().acceptRide('ride-gone');

      const state = useDriverStore.getState();
      expect(state.rideState).toBe('idle');
      expect(state.incomingRide).toBeNull();
    });

    it('should show generic error for non-race failures', async () => {
      const error = new Error('Server error');
      (error as any).response = {
        status: 500,
        data: { detail: 'Internal server error' },
      };
      api.post.mockRejectedValueOnce(error);

      await useDriverStore.getState().acceptRide('ride-1');

      const state = useDriverStore.getState();
      // Should NOT reset to idle for a 500
      expect(state.error).toBe('Internal server error');
    });
  });

  describe('setIncomingRide uses configuredCountdownSeconds', () => {
    it('should use configured countdown when setting incoming ride', () => {
      useDriverStore.getState().applyDriverConfig({
        ride_offer_timeout_seconds: 20,
      });

      useDriverStore.getState().setIncomingRide({
        ride_id: 'ride-1',
        pickup_address: '123 Main',
        dropoff_address: '456 Elm',
        pickup_lat: 50,
        pickup_lng: -104,
        dropoff_lat: 51,
        dropoff_lng: -105,
        fare: 15,
      });

      expect(useDriverStore.getState().countdownSeconds).toBe(20);
    });
  });
});
