/**
 * SOS 401 exemption (privacy/trust hardening, 2026-06)
 *
 * POST /rides/{id}/emergency accepts EXPIRED access tokens server-side
 * (get_current_user_allow_expired), so a 401 on the SOS endpoint must
 * never tear down the session. Pins in shared/api/client.ts::handleApiError:
 *   1. 401 on /rides/{id}/emergency does NOT call authStore.logout()
 *   2. 401 on /rides/{id}/emergency does NOT clear the in-memory token
 *      (SOSButton's retry loop needs it for the next attempt)
 *   3. 401 on /rides/{id}/emergency does NOT trigger the silent-refresh
 *      dance (its nested /auth/refresh 401 path signs the user out)
 *   4. The error still propagates so SOSButton shows "Alert Not Sent"
 *   5. Other endpoints keep the logout-on-401 behavior
 *   6. /users/emergency-contacts is NOT exempt (normal CRUD surface)
 */

// Must be set up before module import
let _mockFetch: jest.MockedFunction<typeof fetch>;

beforeAll(() => {
  _mockFetch = jest.fn();
  global.fetch = _mockFetch;
});

jest.mock('react-native', () => ({
  Platform: { OS: 'web' },
}));

jest.mock('../../config', () => ({
  API_URL: 'http://localhost:8000',
}));

jest.mock('../../config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'http://localhost:8000' },
}));

jest.mock('../../services/firebase', () => ({
  auth: { currentUser: null, onAuthStateChanged: null },
  isFirebaseConfigured: false,
}));

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(() => Promise.resolve(null)),
  setItemAsync: jest.fn(() => Promise.resolve()),
  deleteItemAsync: jest.fn(() => Promise.resolve()),
}));

const _storage: Record<string, string> = {};
Object.defineProperty(global, 'sessionStorage', {
  value: {
    getItem: (k: string) => _storage[k] ?? null,
    setItem: (k: string, v: string) => { _storage[k] = v; },
    removeItem: (k: string) => { delete _storage[k]; },
  },
  writable: true,
});
Object.defineProperty(global, 'localStorage', {
  value: {
    getItem: (k: string) => _storage[k] ?? null,
    setItem: (k: string, v: string) => { _storage[k] = v; },
    removeItem: (k: string) => { delete _storage[k]; },
  },
  writable: true,
});

const mockLogout = jest.fn();
jest.mock('../../store/authStore', () => ({
  useAuthStore: {
    getState: jest.fn(() => ({
      token: 'valid-token',
      tokenExpiresAt: Date.now() + 15 * 60 * 1000,
      logout: mockLogout,
    })),
  },
}));

import api from '../../api/client';
import { setRefreshCallback, setInMemoryToken, hasAuthToken } from '../../api/client';

const make401Response = () =>
  new Response(JSON.stringify({ detail: 'Token expired' }), {
    status: 401,
    headers: { 'Content-Type': 'application/json' },
  });

beforeEach(() => {
  jest.clearAllMocks();
  setInMemoryToken('rider-token-abc');
  setRefreshCallback(null as any);
  Object.keys(_storage).forEach((k) => delete _storage[k]);
});

describe('shared/api/client — SOS exempt from 401→logout interceptor', () => {
  it('401 on /rides/{id}/emergency does not log out or clear the token', async () => {
    _mockFetch.mockResolvedValue(make401Response());

    await expect(
      api.post('/rides/ride-001/emergency', { message: 'help', latitude: 52.1, longitude: -106.6 }),
    ).rejects.toThrow();

    expect(mockLogout).not.toHaveBeenCalled();
    // In-memory token survives so SOSButton's retry loop can re-send.
    expect(hasAuthToken()).toBe(true);
  });

  it('401 on /rides/{id}/emergency does not attempt a silent refresh', async () => {
    const refreshCallback = jest.fn().mockResolvedValue(true);
    setRefreshCallback(refreshCallback);
    _mockFetch.mockResolvedValue(make401Response());

    await expect(api.post('/rides/ride-002/emergency', { message: 'help' })).rejects.toThrow();

    expect(refreshCallback).not.toHaveBeenCalled();
    expect(_mockFetch).toHaveBeenCalledTimes(1);
  });

  it('401 on a non-SOS endpoint still clears the session (G2 behavior)', async () => {
    _mockFetch.mockResolvedValue(make401Response());

    await expect(api.get('/rides/active')).rejects.toThrow();

    expect(mockLogout).toHaveBeenCalled();
    expect(hasAuthToken()).toBe(false);
  });

  it('401 on /users/emergency-contacts is NOT exempt', async () => {
    _mockFetch.mockResolvedValue(make401Response());

    await expect(api.get('/users/emergency-contacts')).rejects.toThrow();

    expect(mockLogout).toHaveBeenCalled();
  });
});
