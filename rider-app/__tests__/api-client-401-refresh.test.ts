/**
 * Regression: a transient token-refresh failure must NOT escalate to logout.
 *
 * handleApiError's silent-refresh path (401 → refreshTokens → retry) falls
 * through on failure to the G2 catch-all, which used to clear the session
 * unconditionally for any non-SOS 401. refreshTokens() deliberately returns
 * false WITHOUT logging out on network/timeout/5xx so a driver on a flaky
 * connection keeps their session — but G2 then deleted the refresh token
 * anyway, hard-signing the driver out (possibly mid-ride) and forcing a
 * fresh OTP login. G2 must fire only when no refresh could be attempted;
 * once refreshTokens() runs, IT owns the logout decision.
 *
 * Code under test: shared/api/client.ts::handleApiError (401 branches + G2)
 */

import * as SecureStore from 'expo-secure-store';
import api, { setRefreshCallback } from '@shared/api/client';

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'http://localhost:8000' },
}));

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(() => Promise.resolve(null)),
  setItemAsync: jest.fn(() => Promise.resolve()),
  deleteItemAsync: jest.fn(() => Promise.resolve()),
}));

const mockLogout = jest.fn();
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: {
    getState: jest.fn(() => ({ token: null, logout: mockLogout })),
  },
}));

const make401Response = () => ({
  ok: false,
  status: 401,
  json: async () => ({ detail: 'Token expired' }),
  headers: { get: () => null },
});

beforeEach(() => {
  jest.clearAllMocks();
  (global as any).fetch = jest.fn(async () => make401Response());
});

afterEach(() => {
  // _refreshCallback is module state — clear it so tests stay independent.
  setRefreshCallback(null as never);
});

describe('401 silent-refresh vs G2 logout backstop', () => {
  it('keeps the session when the refresh fails transiently (refresh returned false without logging out)', async () => {
    // Models refreshTokens() hitting a 503/timeout: resolves false, no logout.
    setRefreshCallback(jest.fn(async () => false));

    await expect(api.get('/rides/estimates')).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(mockLogout).not.toHaveBeenCalled();
    expect(SecureStore.deleteItemAsync).not.toHaveBeenCalled();
  });

  it('logs out exactly once on definitive rejection — by refreshTokens itself, not G2', async () => {
    // Models refreshTokens() getting a 401 from /auth/refresh: it calls
    // logout() internally and resolves false.
    setRefreshCallback(
      jest.fn(async () => {
        mockLogout();
        return false;
      }),
    );

    await expect(api.get('/rides/estimates')).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(mockLogout).toHaveBeenCalledTimes(1);
  });

  it('retries the original request when the refresh succeeds', async () => {
    (global as any).fetch = jest
      .fn()
      .mockResolvedValueOnce(make401Response())
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
        headers: { get: () => null },
      });
    setRefreshCallback(jest.fn(async () => true));

    await expect(api.get('/rides/estimates')).resolves.toMatchObject({ data: { ok: true } });
    expect((global as any).fetch).toHaveBeenCalledTimes(2);
    expect(mockLogout).not.toHaveBeenCalled();
  });

  it('G2 backstop still clears the session when no refresh callback is registered (cold start)', async () => {
    // No setRefreshCallback → the silent-refresh block is skipped entirely.
    await expect(api.get('/rides/estimates')).rejects.toMatchObject({
      response: { status: 401 },
    });

    expect(mockLogout).toHaveBeenCalledTimes(1);
    expect(SecureStore.deleteItemAsync).toHaveBeenCalledWith('auth_token');
  });
});
