/**
 * P1-11: Token refresh mid-trip (E11)
 *
 * Pins the automatic token-refresh behavior in shared/api/client.ts.
 * When an API call returns 401, the client must:
 *   1. Call the registered refresh callback once
 *   2. Retry the original request with the new token
 *   3. If refresh fails, throw the 401 (session cleared by handleApiError)
 *   4. Never trigger a refresh loop on /auth/refresh itself
 *   5. Deduplicate concurrent refresh calls (one in-flight at a time)
 *
 * Code under test: shared/api/client.ts::handleApiError + setRefreshCallback
 */

// Must be set up before module import
let _mockFetch: jest.MockedFunction<typeof fetch>;

beforeAll(() => {
  _mockFetch = jest.fn();
  global.fetch = _mockFetch;
});

// Mock platform + firebase before importing the module
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

// Prevent actual SecureStore calls
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(() => Promise.resolve(null)),
  setItemAsync: jest.fn(() => Promise.resolve()),
  deleteItemAsync: jest.fn(() => Promise.resolve()),
}));

// Stub sessionStorage / localStorage for web path
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

import {
  setRefreshCallback,
  setInMemoryToken,
  ensureFreshToken,
} from '../../api/client';

import api from '../../api/client';

// Mock authStore for ensureFreshToken tests. `mockLogout` is module-scoped
// (the `mock` prefix lets the jest.mock factory reference it) and STABLE
// across getState() calls so a test can assert whether the global 401
// interceptor forced a logout.
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

const makeOkResponse = (body: any = {}) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });

const make401Response = () =>
  new Response(JSON.stringify({ detail: 'Token expired' }), {
    status: 401,
    headers: { 'Content-Type': 'application/json', 'x-request-id': 'req-001' },
  });

beforeEach(() => {
  jest.clearAllMocks();
  setInMemoryToken('valid-token-xyz');
  setRefreshCallback(null as any); // reset between tests
  Object.keys(_storage).forEach((k) => delete _storage[k]);
});

describe('shared/api/client — token refresh mid-trip (P1-11 / E11)', () => {
  it('retries the original request after a successful token refresh', async () => {
    const refreshCallback = jest.fn().mockResolvedValue(true);
    setRefreshCallback(refreshCallback);
    setInMemoryToken('new-token-after-refresh');

    _mockFetch
      .mockResolvedValueOnce(make401Response()) // first call → 401
      .mockResolvedValueOnce(makeOkResponse({ active: true })); // retry → 200

    const result = await api.get('/rides/active');

    expect(refreshCallback).toHaveBeenCalledTimes(1);
    expect(_mockFetch).toHaveBeenCalledTimes(2);
    expect(result.data).toEqual({ active: true });
  });

  it('does not retry when refresh callback returns false', async () => {
    const refreshCallback = jest.fn().mockResolvedValue(false);
    setRefreshCallback(refreshCallback);

    _mockFetch.mockResolvedValue(make401Response());

    await expect(api.get('/rides/active')).rejects.toThrow();

    expect(refreshCallback).toHaveBeenCalledTimes(1);
    // Only the initial call — no retry
    expect(_mockFetch).toHaveBeenCalledTimes(1);
  });

  it('does NOT force a logout when refresh fails transiently (returns false)', async () => {
    // A transient refresh failure (network drop / timeout / 5xx) surfaces as
    // the callback resolving false. The real authStore.refreshTokens keeps the
    // session in that case; the interceptor must not override that decision by
    // hard-logging-out at the global 401 handler. Before this fix, a single
    // dropped connection mid-session bounced the user to /login.
    const refreshCallback = jest.fn().mockResolvedValue(false);
    setRefreshCallback(refreshCallback);

    _mockFetch.mockResolvedValue(make401Response());

    await expect(api.get('/notifications')).rejects.toThrow();

    expect(refreshCallback).toHaveBeenCalledTimes(1);
    expect(mockLogout).not.toHaveBeenCalled();
  });

  it('DOES force a logout on a 401 when no refresh path exists', async () => {
    // With no refresh callback the interceptor can't recover, so the global
    // 401 handler still clears the session (this behavior is unchanged).
    setRefreshCallback(null as any);
    _mockFetch.mockResolvedValue(make401Response());

    await expect(api.get('/notifications')).rejects.toThrow();
    expect(mockLogout).toHaveBeenCalledTimes(1);
  });

  it('does not trigger a refresh loop when /auth/refresh itself returns 401', async () => {
    const refreshCallback = jest.fn().mockResolvedValue(false);
    setRefreshCallback(refreshCallback);

    _mockFetch.mockResolvedValue(make401Response());

    // Calling /auth/refresh directly must not call refreshCallback — it
    // should reject (the server said the refresh token is invalid).
    await expect(api.post('/auth/refresh', { refresh_token: 'bad-token' }))
      .rejects.toBeDefined();

    expect(refreshCallback).not.toHaveBeenCalled();
    // Only the single fetch for /auth/refresh — no dedup loop
    expect(_mockFetch).toHaveBeenCalledTimes(1);
  });

  it('does not refresh if no callback is registered', async () => {
    setRefreshCallback(null as any);
    _mockFetch.mockResolvedValue(make401Response());

    await expect(api.get('/rides/active')).rejects.toThrow();
    expect(_mockFetch).toHaveBeenCalledTimes(1);
  });

  it('deduplicates concurrent refresh calls — only one refresh in-flight', async () => {
    let resolveRefresh!: (v: boolean) => void;
    const refreshPromise = new Promise<boolean>((res) => { resolveRefresh = res; });
    const refreshCallback = jest.fn().mockReturnValue(refreshPromise);
    setRefreshCallback(refreshCallback);

    // Simulate two concurrent 401s
    _mockFetch
      .mockResolvedValueOnce(make401Response()) // request A → 401
      .mockResolvedValueOnce(make401Response()) // request B → 401
      .mockResolvedValue(makeOkResponse({ ok: true })); // retries → 200

    const [reqA, reqB] = [api.get('/rides/active'), api.get('/rides/active')];

    // Let both requests start and hit the 401 path before refresh resolves
    await new Promise((r) => setTimeout(r, 10));
    resolveRefresh(true);

    await Promise.allSettled([reqA, reqB]);

    // Refresh must only be called once, not once per 401
    expect(refreshCallback).toHaveBeenCalledTimes(1);
  });
});

describe('ensureFreshToken — proactive token refresh', () => {
  const { useAuthStore } = require('../../store/authStore');

  beforeEach(() => {
    setRefreshCallback(null as any);
    (useAuthStore.getState as jest.Mock).mockReturnValue({
      token: 'valid-token',
      tokenExpiresAt: Date.now() + 15 * 60 * 1000,
    });
  });

  it('no-ops when token has >2 min remaining', async () => {
    const refreshCallback = jest.fn().mockResolvedValue(true);
    setRefreshCallback(refreshCallback);

    (useAuthStore.getState as jest.Mock).mockReturnValue({
      token: 'valid-token',
      tokenExpiresAt: Date.now() + 10 * 60 * 1000, // 10 min left
    });

    await ensureFreshToken();
    expect(refreshCallback).not.toHaveBeenCalled();
  });

  it('refreshes when token has <2 min remaining', async () => {
    const refreshCallback = jest.fn().mockResolvedValue(true);
    setRefreshCallback(refreshCallback);

    (useAuthStore.getState as jest.Mock).mockReturnValue({
      token: 'valid-token',
      tokenExpiresAt: Date.now() + 60 * 1000, // 1 min left (< 2 min buffer)
    });

    await ensureFreshToken();
    expect(refreshCallback).toHaveBeenCalledTimes(1);
  });

  it('refreshes when token is already expired', async () => {
    const refreshCallback = jest.fn().mockResolvedValue(true);
    setRefreshCallback(refreshCallback);

    (useAuthStore.getState as jest.Mock).mockReturnValue({
      token: 'valid-token',
      tokenExpiresAt: Date.now() - 5000, // expired 5s ago
    });

    await ensureFreshToken();
    expect(refreshCallback).toHaveBeenCalledTimes(1);
  });

  it('no-ops when no callback registered', async () => {
    setRefreshCallback(null as any);

    (useAuthStore.getState as jest.Mock).mockReturnValue({
      token: 'valid-token',
      tokenExpiresAt: Date.now() + 30 * 1000,
    });

    // Should not throw
    await ensureFreshToken();
  });

  it('no-ops when not authenticated', async () => {
    const refreshCallback = jest.fn().mockResolvedValue(true);
    setRefreshCallback(refreshCallback);

    (useAuthStore.getState as jest.Mock).mockReturnValue({
      token: null,
      tokenExpiresAt: null,
    });

    await ensureFreshToken();
    expect(refreshCallback).not.toHaveBeenCalled();
  });

  it('swallows refresh errors without throwing', async () => {
    const refreshCallback = jest.fn().mockRejectedValue(new Error('network'));
    setRefreshCallback(refreshCallback);

    (useAuthStore.getState as jest.Mock).mockReturnValue({
      token: 'valid-token',
      tokenExpiresAt: Date.now() + 30 * 1000,
    });

    // Must not throw
    await ensureFreshToken();
    expect(refreshCallback).toHaveBeenCalledTimes(1);
  });

  it('piggybacks on existing _refreshPromise from 401 handler', async () => {
    let resolveRefresh!: (v: boolean) => void;
    const refreshPromise = new Promise<boolean>((res) => { resolveRefresh = res; });
    const refreshCallback = jest.fn().mockReturnValue(refreshPromise);
    setRefreshCallback(refreshCallback);

    (useAuthStore.getState as jest.Mock).mockReturnValue({
      token: 'valid-token',
      tokenExpiresAt: Date.now() + 30 * 1000,
    });

    // Start a 401-triggered refresh by calling api.get which returns 401
    _mockFetch
      .mockResolvedValueOnce(make401Response())
      .mockResolvedValue(makeOkResponse({ ok: true }));

    const apiCall = api.get('/rides/active');
    await new Promise((r) => setTimeout(r, 10));

    // Now call ensureFreshToken while 401 refresh is in-flight
    const proactive = ensureFreshToken();
    resolveRefresh(true);

    await Promise.allSettled([apiCall, proactive]);

    // Should only have called refreshCallback once — not twice
    expect(refreshCallback).toHaveBeenCalledTimes(1);
  });
});
