// Mock dependencies before importing store
import { useRideStore } from '../rideStore';

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(), get: jest.fn(), put: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));
jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: { id: 'user-abc' } })) },
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

describe('rideStore — WebSocket-driven updates', () => {
  beforeEach(() => {
    useRideStore.setState({
      currentRide: null,
      currentDriver: null,
      chatMessages: [],
      isLoading: false,
      error: null,
    });
  });

  describe('updateDriverLocation', () => {
    it('should update currentDriver lat/lng', () => {
      useRideStore.setState({
         
        currentDriver: { id: 'driver_1', name: 'Jane', rating: 4.9, lat: 50.0, lng: -104.0 } as any,
      });

      useRideStore.getState().updateDriverLocation(51.5, -105.5, 30, 180);

      const driver = useRideStore.getState().currentDriver;
      expect(driver?.lat).toBe(51.5);
      expect(driver?.lng).toBe(-105.5);
      expect(driver?.speed).toBe(30);
      expect(driver?.heading).toBe(180);
      // Other fields untouched
      expect(driver?.name).toBe('Jane');
      expect(driver?.rating).toBe(4.9);
    });

    it('should noop without currentDriver', () => {
      useRideStore.getState().updateDriverLocation(51.5, -105.5);
      expect(useRideStore.getState().currentDriver).toBeNull();
    });

    it('should handle null speed and heading', () => {
      useRideStore.setState({
         
        currentDriver: { id: 'driver_1', lat: 50, lng: -104 } as any,
      });

      useRideStore.getState().updateDriverLocation(51, -105, null, null);

      const driver = useRideStore.getState().currentDriver;
      expect(driver?.lat).toBe(51);
      expect(driver?.lng).toBe(-105);
    });
  });

  describe('applyRideStatusFromWS', () => {
    it('should update matching ride status', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1', status: 'driver_assigned' } as any,
      });

      useRideStore.getState().applyRideStatusFromWS('ride_1', 'driver_arrived');

      expect(useRideStore.getState().currentRide?.status).toBe('driver_arrived');
    });

    it('should ignore wrong ride id', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1', status: 'driver_assigned' } as any,
      });

      useRideStore.getState().applyRideStatusFromWS('ride_999', 'completed');

      expect(useRideStore.getState().currentRide?.status).toBe('driver_assigned');
    });

    it('should merge extra fields', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1', status: 'in_progress' } as any,
      });

      useRideStore.getState().applyRideStatusFromWS('ride_1', 'completed', {
        total_fare: 22.50,
      });

      const ride = useRideStore.getState().currentRide;
      expect(ride?.status).toBe('completed');
      expect((ride as any)?.total_fare).toBe(22.50);
    });

    it('should noop without currentRide', () => {
      useRideStore.getState().applyRideStatusFromWS('ride_1', 'completed');
      expect(useRideStore.getState().currentRide).toBeNull();
    });
  });

  describe('addChatMessage', () => {
    it('should append a message', () => {
      useRideStore.getState().addChatMessage({ id: 'm1', ride_id: 'ride_1', text: 'Hello', sender: 'rider', timestamp: '' });

      expect(useRideStore.getState().chatMessages).toHaveLength(1);
      expect(useRideStore.getState().chatMessages[0].text).toBe('Hello');
    });

    it('should deduplicate by id', () => {
      useRideStore.getState().addChatMessage({ id: 'm1', ride_id: 'ride_1', text: 'Hello', sender: 'rider', timestamp: '' });
      useRideStore.getState().addChatMessage({ id: 'm1', ride_id: 'ride_1', text: 'Hello', sender: 'rider', timestamp: '' });

      expect(useRideStore.getState().chatMessages).toHaveLength(1);
    });

    it('should allow different ids', () => {
      useRideStore.getState().addChatMessage({ id: 'm1', ride_id: 'ride_1', text: 'Hello', sender: 'rider', timestamp: '' });
      useRideStore.getState().addChatMessage({ id: 'm2', ride_id: 'ride_1', text: 'Hi back', sender: 'driver', timestamp: '' });

      expect(useRideStore.getState().chatMessages).toHaveLength(2);
    });
  });

  describe('clearRide clears chat', () => {
    it('should clear chatMessages on clearRide', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1' } as any,
        chatMessages: [{ id: 'm1', ride_id: 'ride_1', text: 'test', sender: 'rider', timestamp: '' }],
      });

      useRideStore.getState().clearRide();

      expect(useRideStore.getState().chatMessages).toHaveLength(0);
      expect(useRideStore.getState().currentRide).toBeNull();
    });
  });

  // R-P1-24: driver_timeout, ride_cancelled, WS/poll race condition tests
  describe('driver_timeout via applyRideStatusFromWS', () => {
    it('should revert status to searching on driver_timeout', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1', status: 'driver_assigned' } as any,
      });

      useRideStore.getState().applyRideStatusFromWS('ride_1', 'searching');

      expect(useRideStore.getState().currentRide?.status).toBe('searching');
    });

    it('should noop driver_timeout when no matching ride', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1', status: 'driver_assigned' } as any,
      });

      useRideStore.getState().applyRideStatusFromWS('ride_999', 'searching');

      expect(useRideStore.getState().currentRide?.status).toBe('driver_assigned');
    });
  });

  describe('ride_cancelled via clearRide', () => {
    it('should clear ride + driver + chat on ride_cancelled', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1', status: 'driver_assigned' } as any,
        currentDriver: { id: 'driver_1', name: 'Jane' } as any,
        chatMessages: [{ id: 'm1', ride_id: 'ride_1', text: 'On my way', sender: 'driver', timestamp: '' }],
      });

      useRideStore.getState().clearRide();

      expect(useRideStore.getState().currentRide).toBeNull();
      expect(useRideStore.getState().currentDriver).toBeNull();
      expect(useRideStore.getState().chatMessages).toHaveLength(0);
    });
  });

  describe('WS/poll race: last-write-wins on status', () => {
    it('applyRideStatusFromWS after a poll should use latest status', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1', status: 'driver_assigned' } as any,
      });

      // Simulate poll setting a later status
      useRideStore.setState({
        currentRide: { id: 'ride_1', status: 'driver_arrived' } as any,
      });

      // WS arrives with stale status — should not downgrade
      useRideStore.getState().applyRideStatusFromWS('ride_1', 'driver_arrived');

      expect(useRideStore.getState().currentRide?.status).toBe('driver_arrived');
    });
  });
});
