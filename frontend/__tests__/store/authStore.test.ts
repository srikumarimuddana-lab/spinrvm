import { useAuthStore } from '../../store/authStore';

// Mock the API client
jest.mock('../../api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
  },
}));

const api = require('../../api/client').default;

describe('authStore', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      driver: null,
      isDriverMode: false,
      token: null,
      isLoading: false,
      isInitialized: false,
      error: null,
    });
    jest.clearAllMocks();
  });

  describe('initial state', () => {
    it('should have correct initial state', () => {
      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.driver).toBeNull();
      expect(state.isDriverMode).toBe(false);
      expect(state.token).toBeNull();
      expect(state.isLoading).toBe(false);
      expect(state.error).toBeNull();
    });
  });

  describe('createProfile', () => {
    it('should create profile successfully', async () => {
      const mockUser = {
        id: 'user-1',
        phone: '+13061234567',
        first_name: 'John',
        last_name: 'Doe',
        email: 'john@test.com',
        role: 'rider',
        created_at: '2024-01-01',
        profile_complete: true,
      };
      api.post.mockResolvedValueOnce({ data: mockUser });

      await useAuthStore.getState().createProfile({
        first_name: 'John',
        last_name: 'Doe',
        email: 'john@test.com',
        gender: 'male',
      });

      expect(useAuthStore.getState().user).toEqual(mockUser);
      expect(useAuthStore.getState().isLoading).toBe(false);
    });

    it('should handle profile creation error', async () => {
      const error = new Error('Failed');
      (error as any).response = { data: { detail: 'Phone already registered' } };
      api.post.mockRejectedValueOnce(error);

      await expect(
        useAuthStore.getState().createProfile({
          first_name: 'John',
          last_name: 'Doe',
          email: 'john@test.com',
          gender: 'male',
        })
      ).rejects.toThrow('Phone already registered');

      expect(useAuthStore.getState().error).toBe('Phone already registered');
      expect(useAuthStore.getState().isLoading).toBe(false);
    });
  });

  describe('registerDriver', () => {
    it('should register driver and update user role', async () => {
      const mockDriver = { id: 'driver-1', user_id: 'user-1', name: 'John' };
      api.post.mockResolvedValueOnce({ data: mockDriver });

      useAuthStore.setState({
        user: {
          id: 'user-1',
          phone: '+13061234567',
          role: 'rider',
          created_at: '2024-01-01',
          profile_complete: true,
        },
      });

      await useAuthStore.getState().registerDriver({ vehicle_make: 'Toyota' });

      const state = useAuthStore.getState();
      expect(state.driver).toEqual(mockDriver);
      expect(state.user?.role).toBe('driver');
      expect(state.user?.is_driver).toBe(true);
      expect(state.isDriverMode).toBe(true);
    });
  });

  describe('toggleDriverMode', () => {
    it('should toggle driver mode on when driver data exists', () => {
      useAuthStore.setState({
        isDriverMode: false,
        driver: { id: 'driver-1' } as any,
      });

      useAuthStore.getState().toggleDriverMode();
      expect(useAuthStore.getState().isDriverMode).toBe(true);
    });

    it('should toggle driver mode off', () => {
      useAuthStore.setState({
        isDriverMode: true,
        driver: { id: 'driver-1' } as any,
      });

      useAuthStore.getState().toggleDriverMode();
      expect(useAuthStore.getState().isDriverMode).toBe(false);
    });
  });

  describe('updateDriverStatus', () => {
    it('should update driver online status', async () => {
      api.post.mockResolvedValueOnce({});
      useAuthStore.setState({
        driver: { id: 'driver-1', is_online: false } as any,
      });

      await useAuthStore.getState().updateDriverStatus(true);

      expect(api.post).toHaveBeenCalledWith('/drivers/status?is_online=true');
      expect(useAuthStore.getState().driver?.is_online).toBe(true);
    });
  });

  describe('logout', () => {
    it('should clear all auth state', async () => {
      useAuthStore.setState({
        user: { id: 'user-1' } as any,
        driver: { id: 'driver-1' } as any,
        token: 'some-token',
        isDriverMode: true,
      });

      await useAuthStore.getState().logout();

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.driver).toBeNull();
      expect(state.token).toBeNull();
      expect(state.isDriverMode).toBe(false);
    });
  });

  describe('clearError', () => {
    it('should clear error state', () => {
      useAuthStore.setState({ error: 'Some error' });
      useAuthStore.getState().clearError();
      expect(useAuthStore.getState().error).toBeNull();
    });
  });
});
