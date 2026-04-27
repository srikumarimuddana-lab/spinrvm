import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useAuthStore } from '@/store/authStore';

describe('authStore', () => {
  beforeEach(() => {
    // Reset store state
    useAuthStore.setState({
      user: null,
      token: null,
      csrfToken: null,
      isAuthenticated: false,
      isLoading: false,
    });
    // Default: fire-and-forget calls (setAuthCookie, clearAuthCookie) resolve silently.
    // Tests that need specific response behaviour override with mockResolvedValueOnce.
    (global.fetch as ReturnType<typeof vi.fn>).mockReset().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    });
  });

  describe('initial state', () => {
    it('should start unauthenticated', () => {
      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.token).toBeNull();
      expect(state.csrfToken).toBeNull();
      expect(state.isAuthenticated).toBe(false);
    });
  });

  describe('setUser', () => {
    it('should set user and mark as authenticated', () => {
      const user = {
        id: 'admin-1',
        email: 'admin@spinr.ca',
        role: 'super_admin',
      };

      useAuthStore.getState().setUser(user);

      const state = useAuthStore.getState();
      expect(state.user).toEqual(user);
      expect(state.isAuthenticated).toBe(true);
      expect(state.isLoading).toBe(false);
    });

    it('should clear authentication when user is null', () => {
      useAuthStore.getState().setUser({
        id: 'admin-1',
        email: 'admin@spinr.ca',
        role: 'super_admin',
      });
      useAuthStore.getState().setUser(null);

      expect(useAuthStore.getState().isAuthenticated).toBe(false);
    });
  });

  describe('setToken', () => {
    it('should set auth token', () => {
      useAuthStore.getState().setToken('jwt-token-123');
      expect(useAuthStore.getState().token).toBe('jwt-token-123');
    });

    it('should clear token', () => {
      useAuthStore.getState().setToken('jwt-token-123');
      useAuthStore.getState().setToken(null);
      expect(useAuthStore.getState().token).toBeNull();
    });
  });

  describe('setLoading', () => {
    it('should set loading state', () => {
      useAuthStore.getState().setLoading(true);
      expect(useAuthStore.getState().isLoading).toBe(true);

      useAuthStore.getState().setLoading(false);
      expect(useAuthStore.getState().isLoading).toBe(false);
    });
  });

  describe('setCsrfToken', () => {
    it('should store and clear the CSRF token', () => {
      useAuthStore.getState().setCsrfToken('abc123');
      expect(useAuthStore.getState().csrfToken).toBe('abc123');

      useAuthStore.getState().setCsrfToken(null);
      expect(useAuthStore.getState().csrfToken).toBeNull();
    });
  });

  describe('logout', () => {
    it('should clear all auth state including csrfToken', () => {
      // Set up authenticated state
      useAuthStore.setState({
        user: { id: 'admin-1', email: 'admin@spinr.ca', role: 'super_admin' },
        token: 'jwt-token',
        csrfToken: 'csrf-abc',
        isAuthenticated: true,
      });

      useAuthStore.getState().logout();

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.token).toBeNull();
      expect(state.csrfToken).toBeNull();
      expect(state.isAuthenticated).toBe(false);
      expect(state.isLoading).toBe(false);
    });
  });

  describe('silentRefresh', () => {
    it('should store new csrf_token from refresh response', async () => {
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({
          token: 'new-access-token',
          access_expires_at: new Date(Date.now() + 900_000).toISOString(),
          csrf_token: 'new-csrf-token',
        }),
      });

      useAuthStore.setState({ csrfToken: 'old-csrf-token', isAuthenticated: true });

      await useAuthStore.getState().silentRefresh();

      const state = useAuthStore.getState();
      expect(state.token).toBe('new-access-token');
      expect(state.csrfToken).toBe('new-csrf-token');
    });

    it('should logout on non-ok refresh response', async () => {
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      useAuthStore.setState({ csrfToken: 'csrf-abc', isAuthenticated: true });

      await useAuthStore.getState().silentRefresh();

      expect(useAuthStore.getState().isAuthenticated).toBe(false);
      expect(useAuthStore.getState().csrfToken).toBeNull();
    });
  });

  describe('checkAuth', () => {
    it('should set loading to false when no token exists', async () => {
      useAuthStore.setState({ token: null, isLoading: true });

      await useAuthStore.getState().checkAuth();

      expect(useAuthStore.getState().isLoading).toBe(false);
    });

    it('should validate token and set user on success', async () => {
      const mockUser = { id: 'admin-1', email: 'admin@spinr.ca', role: 'super_admin' };
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ authenticated: true, user: mockUser }),
      });

      useAuthStore.setState({ token: 'valid-token', isLoading: true });

      await useAuthStore.getState().checkAuth();

      const state = useAuthStore.getState();
      expect(state.user).toEqual(mockUser);
      expect(state.isAuthenticated).toBe(true);
      expect(state.isLoading).toBe(false);
    });

    it('should logout on 401 response', async () => {
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        ok: false,
        status: 401,
      });

      useAuthStore.setState({ token: 'expired-token', isAuthenticated: true });

      await useAuthStore.getState().checkAuth();

      expect(useAuthStore.getState().isAuthenticated).toBe(false);
      expect(useAuthStore.getState().token).toBeNull();
    });

    it('should logout on network error', async () => {
      (global.fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('Network error'));

      useAuthStore.setState({ token: 'some-token', isAuthenticated: true });

      await useAuthStore.getState().checkAuth();

      expect(useAuthStore.getState().isAuthenticated).toBe(false);
    });

    it('should logout when server says not authenticated', async () => {
      (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ authenticated: false }),
      });

      useAuthStore.setState({ token: 'token', isAuthenticated: true });

      await useAuthStore.getState().checkAuth();

      expect(useAuthStore.getState().isAuthenticated).toBe(false);
    });
  });
});
