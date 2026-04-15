import { useDriverStore } from '../../store/driverStore';

// The api mock is set up via moduleNameMapper in jest.config.js
const api = jest.requireMock('@shared/api/client').default;

describe('driverStore', () => {
  beforeEach(() => {
    useDriverStore.setState({
      rideState: 'idle',
      incomingRide: null,
      activeRide: null,
      completedRide: null,
      countdownSeconds: 0,
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
      isLoading: false,
      error: null,
    });
    jest.clearAllMocks();
  });

  const mockIncomingRide = {
    ride_id: 'ride-1',
    pickup_address: '123 Main St',
    dropoff_address: '456 Elm Ave',
    pickup_lat: 50.45,
    pickup_lng: -104.6,
    dropoff_lat: 50.46,
    dropoff_lng: -104.7,
    fare: 15.50,
    distance_km: 5.2,
    duration_minutes: 12,
    rider_name: 'Jane Doe',
    rider_rating: 4.8,
  };

  describe('setIncomingRide', () => {
    it('should set incoming ride and change state to ride_offered', () => {
      useDriverStore.getState().setIncomingRide(mockIncomingRide);

      const state = useDriverStore.getState();
      expect(state.incomingRide).toEqual(mockIncomingRide);
      expect(state.rideState).toBe('ride_offered');
      expect(state.countdownSeconds).toBe(15);
    });

    it('should reset to idle when set to null', () => {
      useDriverStore.getState().setIncomingRide(mockIncomingRide);
      useDriverStore.getState().setIncomingRide(null);

      const state = useDriverStore.getState();
      expect(state.incomingRide).toBeNull();
      expect(state.rideState).toBe('idle');
      expect(state.countdownSeconds).toBe(0);
    });
  });

  describe('setCountdown', () => {
    it('should update countdown seconds', () => {
      useDriverStore.getState().setCountdown(10);
      expect(useDriverStore.getState().countdownSeconds).toBe(10);
    });

    it('should auto-decline when countdown reaches 0 during ride_offered', () => {
      useDriverStore.getState().setIncomingRide(mockIncomingRide);
      api.post.mockResolvedValueOnce({});

      useDriverStore.getState().setCountdown(0);

      expect(api.post).toHaveBeenCalledWith('/drivers/rides/ride-1/decline');
    });
  });

  describe('acceptRide', () => {
    it('should accept ride and transition to navigating_to_pickup', async () => {
      api.post.mockResolvedValueOnce({ data: {} });
      api.get.mockResolvedValueOnce({ data: { ride: { id: 'ride-1', status: 'driver_assigned' } } });

      useDriverStore.getState().setIncomingRide(mockIncomingRide);
      await useDriverStore.getState().acceptRide('ride-1');

      expect(api.post).toHaveBeenCalledWith('/drivers/rides/ride-1/accept');
      expect(useDriverStore.getState().incomingRide).toBeNull();
      expect(useDriverStore.getState().countdownSeconds).toBe(0);
    });

    it('should handle accept error', async () => {
      const error = new Error('Already accepted');
      (error as any).response = { data: { detail: 'Ride already accepted' } };
      api.post.mockRejectedValueOnce(error);

      await useDriverStore.getState().acceptRide('ride-1');

      expect(useDriverStore.getState().error).toBe('Ride already accepted');
    });
  });

  describe('declineRide', () => {
    it('should decline ride and reset state', async () => {
      api.post.mockResolvedValueOnce({});
      useDriverStore.getState().setIncomingRide(mockIncomingRide);

      await useDriverStore.getState().declineRide('ride-1');

      const state = useDriverStore.getState();
      expect(state.rideState).toBe('idle');
      expect(state.incomingRide).toBeNull();
      expect(state.countdownSeconds).toBe(0);
    });
  });

  describe('completeRide', () => {
    it('should complete ride and transition to trip_completed', async () => {
      const mockCompletedRide = { id: 'ride-1', status: 'completed', total_fare: 15.50 };
      api.post.mockResolvedValueOnce({ data: mockCompletedRide });

      await useDriverStore.getState().completeRide('ride-1');

      const state = useDriverStore.getState();
      expect(state.rideState).toBe('trip_completed');
      expect(state.completedRide).toEqual(mockCompletedRide);
      expect(state.activeRide).toBeNull();
    });
  });

  describe('cancelRide', () => {
    it('should cancel ride and reset state', async () => {
      api.post.mockResolvedValueOnce({});

      await useDriverStore.getState().cancelRide('ride-1', 'Rider not found');

      expect(api.post).toHaveBeenCalledWith('/drivers/rides/ride-1/cancel?reason=Rider%20not%20found');
      expect(useDriverStore.getState().rideState).toBe('idle');
      expect(useDriverStore.getState().activeRide).toBeNull();
    });
  });

  describe('fetchActiveRide', () => {
    it('should set rideState based on ride status - driver_assigned', async () => {
      api.get.mockResolvedValueOnce({
        data: { ride: { id: 'ride-1', status: 'driver_assigned' } },
      });

      await useDriverStore.getState().fetchActiveRide();

      expect(useDriverStore.getState().rideState).toBe('navigating_to_pickup');
    });

    it('should set rideState for driver_arrived', async () => {
      api.get.mockResolvedValueOnce({
        data: { ride: { id: 'ride-1', status: 'driver_arrived' } },
      });

      await useDriverStore.getState().fetchActiveRide();

      expect(useDriverStore.getState().rideState).toBe('arrived_at_pickup');
    });

    it('should set rideState for in_progress', async () => {
      api.get.mockResolvedValueOnce({
        data: { ride: { id: 'ride-1', status: 'in_progress' } },
      });

      await useDriverStore.getState().fetchActiveRide();

      expect(useDriverStore.getState().rideState).toBe('trip_in_progress');
    });

    it('should clear activeRide when no active ride exists', async () => {
      api.get.mockResolvedValueOnce({ data: {} });

      await useDriverStore.getState().fetchActiveRide();

      expect(useDriverStore.getState().activeRide).toBeNull();
    });
  });

  describe('resetRideState', () => {
    it('should reset all ride-related state', () => {
      useDriverStore.setState({
        rideState: 'trip_in_progress',
        incomingRide: mockIncomingRide,
        activeRide: { ride: {}, rider: {}, vehicle_type: {} },
        countdownSeconds: 10,
        error: 'some error',
      });

      useDriverStore.getState().resetRideState();

      const state = useDriverStore.getState();
      expect(state.rideState).toBe('idle');
      expect(state.incomingRide).toBeNull();
      expect(state.activeRide).toBeNull();
      expect(state.completedRide).toBeNull();
      expect(state.countdownSeconds).toBe(0);
      expect(state.error).toBeNull();
    });
  });

  describe('fetchEarnings', () => {
    it('should fetch earnings for a period', async () => {
      const mockEarnings = { period: 'day', total_earnings: 150, total_rides: 8 };
      api.get.mockResolvedValueOnce({ data: mockEarnings });

      await useDriverStore.getState().fetchEarnings('day');

      expect(api.get).toHaveBeenCalledWith('/drivers/earnings?period=day');
      expect(useDriverStore.getState().earnings).toEqual(mockEarnings);
    });
  });

  describe('fetchRideHistory', () => {
    it('should fetch ride history with pagination', async () => {
      const mockHistory = { rides: [{ id: 'ride-1' }], total: 1 };
      api.get.mockResolvedValueOnce({ data: mockHistory });

      await useDriverStore.getState().fetchRideHistory(10, 0);

      expect(api.get).toHaveBeenCalledWith('/drivers/rides/history?limit=10&offset=0');
      expect(useDriverStore.getState().rideHistory).toHaveLength(1);
      expect(useDriverStore.getState().historyTotal).toBe(1);
    });
  });

  describe('bank account management', () => {
    it('should fetch bank account', async () => {
      api.get.mockResolvedValueOnce({
        data: { has_bank_account: true, bank_account: { bank_name: 'TD' } },
      });

      await useDriverStore.getState().fetchBankAccount();

      expect(useDriverStore.getState().hasBankAccount).toBe(true);
      expect(useDriverStore.getState().bankAccount?.bank_name).toBe('TD');
    });

    it('should delete bank account', async () => {
      api.delete.mockResolvedValueOnce({});

      const result = await useDriverStore.getState().deleteBankAccount();

      expect(result).toBe(true);
      expect(useDriverStore.getState().hasBankAccount).toBe(false);
      expect(useDriverStore.getState().bankAccount).toBeNull();
    });
  });

  describe('rateRider', () => {
    it('should submit rider rating', async () => {
      api.post.mockResolvedValueOnce({});

      await useDriverStore.getState().rateRider('ride-1', 5, 'Great passenger');

      expect(api.post).toHaveBeenCalledWith('/drivers/rides/ride-1/rate-rider', {
        rating: 5,
        comment: 'Great passenger',
      });
    });
  });

  describe('clearError', () => {
    it('should clear error', () => {
      useDriverStore.setState({ error: 'Some error' });
      useDriverStore.getState().clearError();
      expect(useDriverStore.getState().error).toBeNull();
    });
  });

  describe('acceptRide — race condition handling', () => {
    beforeEach(() => {
      useDriverStore.getState().setIncomingRide(mockIncomingRide);
    });

    it('clears incomingRide and resets to idle on 404 (ride gone)', async () => {
      const err = new Error('Not Found');
      (err as any).response = { status: 404, data: { detail: 'Ride not found' } };
      api.post.mockRejectedValueOnce(err);

      await useDriverStore.getState().acceptRide('ride-1');

      const state = useDriverStore.getState();
      expect(state.incomingRide).toBeNull();
      expect(state.rideState).toBe('idle');
      expect(state.countdownSeconds).toBe(0);
      expect(state.error).toMatch(/already taken/i);
    });

    it('clears incomingRide and resets to idle on 400 "already accepted"', async () => {
      const err = new Error('Bad Request');
      (err as any).response = {
        status: 400,
        data: { detail: 'Ride already accepted by another driver' },
      };
      api.post.mockRejectedValueOnce(err);

      await useDriverStore.getState().acceptRide('ride-1');

      const state = useDriverStore.getState();
      expect(state.incomingRide).toBeNull();
      expect(state.rideState).toBe('idle');
      expect(state.error).toMatch(/already taken/i);
    });

    it('keeps incomingRide on generic network error (retryable)', async () => {
      const err = new Error('Network Error');
      (err as any).response = { status: 500, data: { detail: 'Internal server error' } };
      api.post.mockRejectedValueOnce(err);

      useDriverStore.getState().setIncomingRide(mockIncomingRide);
      await useDriverStore.getState().acceptRide('ride-1');

      // Generic 500 should set error but NOT clear incomingRide
      expect(useDriverStore.getState().error).toBe('Internal server error');
      // rideState was already 'ride_offered' — verify not forcibly reset to idle
      expect(useDriverStore.getState().rideState).toBe('ride_offered');
    });
  });

  describe('applyDriverConfig', () => {
    it('stores configuredCountdownSeconds and configuredPickupRadiusMeters', () => {
      useDriverStore.getState().applyDriverConfig({
        ride_offer_timeout_seconds: 30,
        pickup_radius_meters: 200,
      });

      const state = useDriverStore.getState();
      expect(state.configuredCountdownSeconds).toBe(30);
      expect(state.configuredPickupRadiusMeters).toBe(200);
    });
  });
});
