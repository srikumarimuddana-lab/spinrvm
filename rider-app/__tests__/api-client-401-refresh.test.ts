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

import * as SecureStore from 'expo-secure-store';
import api, { setRefreshCallback } from '@shared/api/client';

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

  // ── Concurrent 401s on the SAME method+url ────────────────────────────
  // _inflight401Retries bounds refresh-retries to one per logical request path.
  // Skipping the retry is right; falling through to G2 is not — G2 signed the
  // user out while the first request's refresh was still succeeding. The driver
  // app trips this on every resume: initialize(), refreshProfile() and the
  // TanStack refetch-on-focus fire duplicate GET /auth/me and GET /drivers/me
  // simultaneously, at exactly the moment the access token is stale.
  //
  // Note both promises get a .catch() attached SYNCHRONOUSLY. The losing request
  // still rejects, and if its rejection is unhandled even briefly, Node reports
  // an unhandledRejection and jest fails the test for the wrong reason.
  const openRefreshGate = () => {
    let resolveRefresh!: (v: boolean) => void;
    const gate = new Promise<boolean>((r) => {
      resolveRefresh = r;
    });
    return { gate, resolveRefresh };
  };

  const okResponse = () => ({
    ok: true,
    status: 200,
    json: async () => ({ ok: true }),
    headers: { get: () => null },
  });

  it('does not let a concurrent 401 on the same url fall through to the G2 logout', async () => {
    const { gate, resolveRefresh } = openRefreshGate();
    setRefreshCallback(jest.fn(() => gate));

    (global as any).fetch = jest
      .fn()
      .mockResolvedValueOnce(make401Response()) // request A → 401, starts refresh
      .mockResolvedValueOnce(make401Response()) // request B → 401, same url
      .mockResolvedValue(okResponse()); // A's retry after refresh

    const settledA = api.get('/rides/estimates').catch((e) => e);
    const settledB = api.get('/rides/estimates').catch((e) => e);

    // Let both requests reach the 401 handler before the refresh resolves.
    await new Promise((r) => setTimeout(r, 10));
    resolveRefresh(true);

    const [a, b] = await Promise.all([settledA, settledB]);

    // The whole point: the refresh succeeded, so the session must survive.
    expect(mockLogout).not.toHaveBeenCalled();
    expect(SecureStore.deleteItemAsync).not.toHaveBeenCalled();

    // The first request recovers via its retry.
    expect(a).toMatchObject({ data: { ok: true } });
    // The second still rejects with its original 401 — accepted behaviour. Its
    // caller retries or surfaces an error; what must NOT happen is a sign-out.
    expect(b).toMatchObject({ response: { status: 401 } });
  });

  it('still logs out exactly once when the shared refresh is definitively rejected', async () => {
    // Guards the other direction: suppressing G2 for the queued request must not
    // suppress a logout that is genuinely warranted. refreshTokens() owns that
    // decision and calls logout() itself — exactly once, not once per 401.
    const { gate, resolveRefresh } = openRefreshGate();
    setRefreshCallback(
      jest.fn(async () => {
        const result = await gate;
        mockLogout(); // models refreshTokens()'s own definitive-rejection logout
        return result;
      }),
    );

    (global as any).fetch = jest.fn(async () => make401Response());

    const settledA = api.get('/rides/estimates').catch((e) => e);
    const settledB = api.get('/rides/estimates').catch((e) => e);

    await new Promise((r) => setTimeout(r, 10));
    resolveRefresh(false);

    await Promise.all([settledA, settledB]);

    expect(mockLogout).toHaveBeenCalledTimes(1);
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
