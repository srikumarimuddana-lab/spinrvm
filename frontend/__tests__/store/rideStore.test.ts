import { useRideStore } from '../../store/rideStore';

// Mock the API client
jest.mock('../../api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
}));

const api = require('../../api/client').default;

describe('rideStore', () => {
  beforeEach(() => {
    // Reset store to initial state
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
      isLoading: false,
      error: null,
    });
    jest.clearAllMocks();
  });

  describe('setPickup / setDropoff', () => {
    it('should set pickup location', () => {
      const location = { address: '123 Main St', lat: 50.45, lng: -104.6 };
      useRideStore.getState().setPickup(location);
      expect(useRideStore.getState().pickup).toEqual(location);
    });

    it('should set dropoff location', () => {
      const location = { address: '456 Elm Ave', lat: 50.46, lng: -104.7 };
      useRideStore.getState().setDropoff(location);
      expect(useRideStore.getState().dropoff).toEqual(location);
    });

    it('should clear pickup when set to null', () => {
      useRideStore.getState().setPickup({ address: 'Test', lat: 0, lng: 0 });
      useRideStore.getState().setPickup(null);
      expect(useRideStore.getState().pickup).toBeNull();
    });
  });

  describe('stops management', () => {
    it('should add a stop', () => {
      const stop = { address: 'Stop 1', lat: 50.47, lng: -104.65 };
      useRideStore.getState().addStop(stop);
      expect(useRideStore.getState().stops).toHaveLength(1);
      expect(useRideStore.getState().stops[0]).toEqual(stop);
    });

    it('should add multiple stops', () => {
      useRideStore.getState().addStop({ address: 'Stop 1', lat: 50.47, lng: -104.65 });
      useRideStore.getState().addStop({ address: 'Stop 2', lat: 50.48, lng: -104.66 });
      expect(useRideStore.getState().stops).toHaveLength(2);
    });

    it('should remove a stop by index', () => {
      useRideStore.getState().addStop({ address: 'Stop 1', lat: 50.47, lng: -104.65 });
      useRideStore.getState().addStop({ address: 'Stop 2', lat: 50.48, lng: -104.66 });
      useRideStore.getState().removeStop(0);
      expect(useRideStore.getState().stops).toHaveLength(1);
      expect(useRideStore.getState().stops[0].address).toBe('Stop 2');
    });

    it('should update a stop at specific index', () => {
      useRideStore.getState().addStop({ address: 'Stop 1', lat: 50.47, lng: -104.65 });
      const updated = { address: 'Updated Stop', lat: 50.50, lng: -104.70 };
      useRideStore.getState().updateStop(0, updated);
      expect(useRideStore.getState().stops[0]).toEqual(updated);
    });
  });

  describe('fetchEstimates', () => {
    it('should not fetch if pickup is missing', async () => {
      useRideStore.setState({ dropoff: { address: 'Dest', lat: 50.46, lng: -104.7 } });
      await useRideStore.getState().fetchEstimates();
      expect(api.post).not.toHaveBeenCalled();
    });

    it('should not fetch if dropoff is missing', async () => {
      useRideStore.setState({ pickup: { address: 'Src', lat: 50.45, lng: -104.6 } });
      await useRideStore.getState().fetchEstimates();
      expect(api.post).not.toHaveBeenCalled();
    });

    it('should fetch estimates when pickup and dropoff are set', async () => {
      const mockEstimates = [
        { vehicle_type: { id: '1', name: 'Standard' }, total_fare: 15.50, available: true },
      ];
      api.post.mockResolvedValueOnce({ data: mockEstimates });

      useRideStore.setState({
        pickup: { address: 'Src', lat: 50.45, lng: -104.6 },
        dropoff: { address: 'Dest', lat: 50.46, lng: -104.7 },
      });

      await useRideStore.getState().fetchEstimates();

      expect(api.post).toHaveBeenCalledWith('/rides/estimate', expect.objectContaining({
        pickup_lat: 50.45,
        pickup_lng: -104.6,
        dropoff_lat: 50.46,
        dropoff_lng: -104.7,
      }));
      expect(useRideStore.getState().estimates).toEqual(mockEstimates);
      expect(useRideStore.getState().isLoading).toBe(false);
    });

    it('should handle estimate fetch error', async () => {
      api.post.mockRejectedValueOnce(new Error('Network error'));

      useRideStore.setState({
        pickup: { address: 'Src', lat: 50.45, lng: -104.6 },
        dropoff: { address: 'Dest', lat: 50.46, lng: -104.7 },
      });

      await useRideStore.getState().fetchEstimates();

      expect(useRideStore.getState().error).toBe('Network error');
      expect(useRideStore.getState().isLoading).toBe(false);
    });
  });

  describe('createRide', () => {
    it('should throw if missing ride details', async () => {
      await expect(useRideStore.getState().createRide('cash')).rejects.toThrow('Missing ride details');
    });

    it('should create a ride successfully', async () => {
      const mockRide = { id: 'ride-1', status: 'searching', total_fare: 15.50 };
      api.post.mockResolvedValueOnce({ data: mockRide });

      useRideStore.setState({
        pickup: { address: 'Src', lat: 50.45, lng: -104.6 },
        dropoff: { address: 'Dest', lat: 50.46, lng: -104.7 },
        selectedVehicle: { id: 'vt-1', name: 'Standard', description: '', icon: '', capacity: 4 },
      });

      const result = await useRideStore.getState().createRide('cash');

      expect(result).toEqual(mockRide);
      expect(useRideStore.getState().currentRide).toEqual(mockRide);
      expect(useRideStore.getState().isLoading).toBe(false);
    });
  });

  describe('cancelRide', () => {
    it('should do nothing if no current ride', async () => {
      await useRideStore.getState().cancelRide();
      expect(api.post).not.toHaveBeenCalled();
    });

    it('should cancel current ride', async () => {
      api.post.mockResolvedValueOnce({});
      useRideStore.setState({
        currentRide: { id: 'ride-1', status: 'searching' } as any,
      });

      await useRideStore.getState().cancelRide();

      expect(api.post).toHaveBeenCalledWith('/rides/ride-1/cancel');
      expect(useRideStore.getState().currentRide).toBeNull();
      expect(useRideStore.getState().currentDriver).toBeNull();
    });
  });

  describe('clearRide', () => {
    it('should reset all ride-related state', () => {
      useRideStore.setState({
        pickup: { address: 'Test', lat: 0, lng: 0 },
        dropoff: { address: 'Test', lat: 0, lng: 0 },
        stops: [{ address: 'Stop', lat: 0, lng: 0 }],
        currentRide: { id: 'ride-1' } as any,
      });

      useRideStore.getState().clearRide();

      const state = useRideStore.getState();
      expect(state.pickup).toBeNull();
      expect(state.dropoff).toBeNull();
      expect(state.stops).toHaveLength(0);
      expect(state.currentRide).toBeNull();
      expect(state.currentDriver).toBeNull();
    });
  });

  describe('recentSearches', () => {
    it('should add a recent search', () => {
      const location = { address: 'Recent Place', lat: 50.45, lng: -104.6 };
      useRideStore.getState().addRecentSearch(location);
      expect(useRideStore.getState().recentSearches).toHaveLength(1);
      expect(useRideStore.getState().recentSearches[0]).toEqual(location);
    });

    it('should avoid duplicate addresses', () => {
      const location = { address: 'Same Place', lat: 50.45, lng: -104.6 };
      useRideStore.getState().addRecentSearch(location);
      useRideStore.getState().addRecentSearch(location);
      expect(useRideStore.getState().recentSearches).toHaveLength(1);
    });

    it('should keep max 10 recent searches', () => {
      for (let i = 0; i < 15; i++) {
        useRideStore.getState().addRecentSearch({
          address: `Place ${i}`,
          lat: 50 + i * 0.01,
          lng: -104 - i * 0.01,
        });
      }
      expect(useRideStore.getState().recentSearches).toHaveLength(10);
    });

    it('should clear recent searches', () => {
      useRideStore.getState().addRecentSearch({ address: 'Test', lat: 0, lng: 0 });
      useRideStore.getState().clearRecentSearches();
      expect(useRideStore.getState().recentSearches).toHaveLength(0);
    });
  });

  describe('selectVehicle', () => {
    it('should set selected vehicle', () => {
      const vehicle = { id: 'vt-1', name: 'Standard', description: '', icon: '', capacity: 4 };
      useRideStore.getState().selectVehicle(vehicle);
      expect(useRideStore.getState().selectedVehicle).toEqual(vehicle);
    });
  });

  describe('clearError', () => {
    it('should clear error state', () => {
      useRideStore.setState({ error: 'Some error' });
      useRideStore.getState().clearError();
      expect(useRideStore.getState().error).toBeNull();
    });
  });

  describe('rateRide', () => {
    it('should call rate endpoint', async () => {
      api.post.mockResolvedValueOnce({});
      await useRideStore.getState().rateRide('ride-1', 5, 'Great ride!', 2.00);
      expect(api.post).toHaveBeenCalledWith('/rides/ride-1/rate', {
        rating: 5,
        comment: 'Great ride!',
        tip_amount: 2.00,
      });
    });

    it('should default tip to 0', async () => {
      api.post.mockResolvedValueOnce({});
      await useRideStore.getState().rateRide('ride-1', 4);
      expect(api.post).toHaveBeenCalledWith('/rides/ride-1/rate', {
        rating: 4,
        comment: undefined,
        tip_amount: 0,
      });
    });
  });
});
