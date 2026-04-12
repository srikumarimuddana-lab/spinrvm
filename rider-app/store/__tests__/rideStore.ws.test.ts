import { useRideStore } from '../../rideStore';

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
        currentDriver: {
          id: 'driver_1',
          name: 'Jane',
          rating: 4.9,
          lat: 50.0,
          lng: -104.0,
        },
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
        currentDriver: { id: 'driver_1', lat: 50, lng: -104 },
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
      useRideStore.getState().addChatMessage({ id: 'm1', text: 'Hello', sender: 'rider' });

      expect(useRideStore.getState().chatMessages).toHaveLength(1);
      expect(useRideStore.getState().chatMessages[0].text).toBe('Hello');
    });

    it('should deduplicate by id', () => {
      useRideStore.getState().addChatMessage({ id: 'm1', text: 'Hello', sender: 'rider' });
      useRideStore.getState().addChatMessage({ id: 'm1', text: 'Hello', sender: 'rider' });

      expect(useRideStore.getState().chatMessages).toHaveLength(1);
    });

    it('should allow different ids', () => {
      useRideStore.getState().addChatMessage({ id: 'm1', text: 'Hello', sender: 'rider' });
      useRideStore.getState().addChatMessage({ id: 'm2', text: 'Hi back', sender: 'driver' });

      expect(useRideStore.getState().chatMessages).toHaveLength(2);
    });
  });

  describe('clearRide clears chat', () => {
    it('should clear chatMessages on clearRide', () => {
      useRideStore.setState({
        currentRide: { id: 'ride_1' } as any,
        chatMessages: [{ id: 'm1', text: 'test' }],
      });

      useRideStore.getState().clearRide();

      expect(useRideStore.getState().chatMessages).toHaveLength(0);
      expect(useRideStore.getState().currentRide).toBeNull();
    });
  });
});
