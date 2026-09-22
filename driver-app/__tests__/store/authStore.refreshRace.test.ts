/**
 * Pins the foreground/background refresh-token rotation-race recovery in
 * shared/store/authStore.ts::refreshTokens.
 *
 * Background: the driver app refreshes the access token from TWO independent
 * JS contexts that share the SecureStore `refresh_token` key —
 *   • the foreground app (authStore.refreshTokens), and
 *   • a headless background location task (utils/backgroundLocation.ts).
 * Refresh tokens are single-use and rotated on every /auth/refresh. When the
 * background task rotates the token, the foreground's in-memory copy goes
 * stale; replaying it makes the backend return 401 (a benign rotation race —
 * NOT a session revocation). Before this fix the foreground reacted to that
 * 401 by logging the user out, which produced persistent 401s on the
 * high-frequency pollers (`GET /notifications`, `POST /drivers/location-batch`)
 * until re-login.
 *
 * Fix: refreshTokens() reads the freshest persisted token first and, on a 401,
 * re-reads storage and retries once with a rotated-forward token before tearing
 * down the session. It only logs out when the token is genuinely dead.
 *
 * Code under test: shared/store/authStore.ts::refreshTokens
 */

import apiClient, {
  setSuppressRefreshSignOut,
} from '../../../shared/api/client';
import { useAuthStore } from '../../../shared/store/authStore';
import { installSessionLock } from '../../../shared/auth/sessionLock';

jest.mock('react-native', () => ({
  Platform: { OS: 'android' },
}));

jest.mock('../../../shared/config/firebaseConfig', () => ({
  auth: {},
}));

jest.mock('firebase/auth', () => ({
  PhoneAuthProvider: { credential: jest.fn() },
  signInWithCredential: jest.fn(),
  signOut: jest.fn(),
}));

const mockSecureStoreBacking: Record<string, string> = {};
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn((k: string) =>
    Promise.resolve(mockSecureStoreBacking[k] ?? null),
  ),
  setItemAsync: jest.fn((k: string, v: string) => {
    mockSecureStoreBacking[k] = v;
    return Promise.resolve();
  }),
  deleteItemAsync: jest.fn((k: string) => {
    delete mockSecureStoreBacking[k];
    return Promise.resolve();
  }),
}));

jest.mock('../../../shared/cache', () => ({
  appCache: {
    set: jest.fn().mockResolvedValue(undefined),
    get: jest.fn().mockResolvedValue(null),
    remove: jest.fn().mockResolvedValue(undefined),
    clearUserCache: jest.fn().mockResolvedValue(undefined),
  },
  CACHE_KEYS: { USER_PROFILE: 'user', DRIVER_PROFILE: 'driver' },
  CACHE_CONFIG: { USER_PROFILE_TTL: 120000 },
}));

jest.mock('../../../shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  setInMemoryToken: jest.fn(),
  setRefreshCallback: jest.fn(),
  setCsrfToken: jest.fn(),
  setSuppressRefreshSignOut: jest.fn(),
  getAuthHeader: jest.fn(() => Promise.resolve(null)),
}));

const mockPost = apiClient.post as jest.Mock;
const mockSetSuppress = setSuppressRefreshSignOut as jest.Mock;

// SpinrApiError-shaped rejection (status on `.response.status` AND `.status`).
const make401 = () => {
  const err: any = new Error('refresh token rejected');
  err.response = { status: 401, data: { detail: 'invalid' } };
  return err;
};

// The REAL shape the client rejects with on the /auth/refresh path: a raw
// fetch Response (HTTP status on `.status`, NO `.response` wrapper). This is
// what production sends to refreshTokens' catch — the retry must detect it.
const make401RawResponse = () => ({ status: 401, ok: false });

// The real backend's RefreshResponse (backend/routes/auth.py) sends
// access_expires_at as an absolute ISO timestamp, never expires_in -- fixtures
// must match that real contract. See Sentry CRIMSON-SMOKE-7445-10F/10Y/SE.
const futureIso = (seconds: number) => new Date(Date.now() + seconds * 1000).toISOString();

beforeEach(() => {
  let tail: Promise<unknown> = Promise.resolve();
  installSessionLock(work => {
    const next = tail.then(work, work);
    tail = next.catch(() => {});
    return next;
  });
  Object.keys(mockSecureStoreBacking).forEach((k) => delete mockSecureStoreBacking[k]);
  mockPost.mockReset();
  mockSetSuppress.mockClear();
  useAuthStore.setState({
    user: null,
    driver: null,
    token: null,
    refreshToken: null,
    tokenExpiresAt: null,
    isLoading: false,
    isInitialized: false,
    error: null,
    isDriverMode: false,
  });
});

describe('authStore.refreshTokens — rotation-race recovery', () => {
  it.each([1, -1])('anchors expires_in to the device clock when it is skewed %s days', async skewDays => {
    const realNow = Date.now();
    jest.useFakeTimers().setSystemTime(realNow + skewDays * 24 * 60 * 60 * 1000);
    try {
      useAuthStore.setState({ token: 'old-access', refreshToken: 'old-refresh' });
      mockSecureStoreBacking.refresh_token = 'old-refresh';
      mockPost.mockResolvedValueOnce({ data: {
        token: 'new-access', refresh_token: 'successor-refresh', expires_in: 900,
        access_expires_at: new Date(realNow + 900_000).toISOString(),
      } }).mockResolvedValueOnce({ data: {
        token: 'next-access', refresh_token: 'next-successor', expires_in: 900,
      } });

      expect(await useAuthStore.getState().refreshTokens()).toBe(true);
      expect(mockSecureStoreBacking.refresh_token).toBe('successor-refresh');
      expect(Number(mockSecureStoreBacking.token_expires_at)).toBe(Date.now() + 900_000);
      mockSecureStoreBacking.token_expires_at = '1';
      useAuthStore.setState({ token: 'expired-access' });
      expect(await useAuthStore.getState().refreshTokens()).toBe(true);
      expect(mockPost).toHaveBeenLastCalledWith('/auth/refresh', { refresh_token: 'successor-refresh' });
    } finally {
      jest.useRealTimers();
    }
  });

  it('keeps the rotated successor and expires immediately for a legacy absolute timestamp behind the device clock', async () => {
    const realNow = Date.now();
    jest.useFakeTimers().setSystemTime(realNow + 24 * 60 * 60 * 1000);
    try {
      useAuthStore.setState({ token: 'old-access', refreshToken: 'old-refresh' });
      mockSecureStoreBacking.refresh_token = 'old-refresh';
      mockPost.mockResolvedValueOnce({
        data: { token: 'new-access', refresh_token: 'successor-refresh', access_expires_at: new Date(realNow + 900_000).toISOString() },
      });
      expect(await useAuthStore.getState().refreshTokens()).toBe(true);
      expect(mockSecureStoreBacking.refresh_token).toBe('successor-refresh');
      expect(Number(mockSecureStoreBacking.token_expires_at)).toBe(Date.now());
    } finally {
      jest.useRealTimers();
    }
  });

  it('ignores an overflowing relative lifetime and falls back to a valid legacy timestamp', async () => {
    useAuthStore.setState({ token: 'old-access', refreshToken: 'old-refresh' });
    mockSecureStoreBacking.refresh_token = 'old-refresh';
    mockPost.mockResolvedValueOnce({ data: {
      token: 'new-access', refresh_token: 'successor-refresh', expires_in: Number.MAX_VALUE,
      access_expires_at: futureIso(900),
    } });
    expect(await useAuthStore.getState().refreshTokens()).toBe(true);
    expect(mockSecureStoreBacking.refresh_token).toBe('successor-refresh');
    expect(Number.isFinite(Number(mockSecureStoreBacking.token_expires_at))).toBe(true);
  });

  it('changes the capture epoch only on sign-in, preserving it through rotation', async () => {
    await useAuthStore.getState().setTokens('access-a', 'refresh-a', 1);
    const epoch = mockSecureStoreBacking['spinr_session_generation'];
    expect(epoch).toEqual(expect.any(String));
    mockPost.mockResolvedValue({ data: { token: 'renewed-a', refresh_token: 'renewed-refresh-a', access_expires_at: futureIso(900) } });
    expect(await useAuthStore.getState().refreshTokens()).toBe(true);
    expect(mockSecureStoreBacking['spinr_session_generation']).toBe(epoch);
    await useAuthStore.getState().setTokens('access-b', 'refresh-b', 900);
    expect(mockSecureStoreBacking['spinr_session_generation']).not.toBe(epoch);
  });

  it('rejects a malformed refresh response instead of persisting corrupted state', async () => {
    // Sentry CRIMSON-SMOKE-7445-10F/10Y/SE class: a response missing/mangling
    // access_expires_at must never be treated as a successful refresh (the old
    // bug computed NaN and silently reported success). Covers the foreground
    // refreshTokens() path, which had no equivalent guard until this fix --
    // backgroundAuth.ts already validated its own response shape.
    useAuthStore.setState({ token: 'old-access', refreshToken: 'old-refresh' });
    mockSecureStoreBacking.refresh_token = 'old-refresh';
    mockPost.mockResolvedValue({ data: { token: 'new-access', refresh_token: 'new-refresh' /* no access_expires_at */ } });
    expect(await useAuthStore.getState().refreshTokens()).toBe(false);
    expect(useAuthStore.getState().token).toBe('old-access');
    expect(mockSecureStoreBacking.refresh_token).toBe('old-refresh');
    expect(mockSecureStoreBacking.token_expires_at).toBeUndefined();
  });

  it('adopts a fresh background credential without rotating it again', async () => {
    useAuthStore.setState({ token: 'old-access', refreshToken: 'old-refresh' });
    Object.assign(mockSecureStoreBacking, { fg_access_token: 'background-access', refresh_token: 'background-refresh', token_expires_at: String(Date.now() + 900_000) });
    expect(await useAuthStore.getState().refreshTokens()).toBe(true);
    expect(useAuthStore.getState().token).toBe('background-access');
    expect(useAuthStore.getState().refreshToken).toBe('background-refresh');
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('does not refresh an explicitly ended session', async () => {
    Object.assign(mockSecureStoreBacking, { spinr_session_ended: '1', refresh_token: 'old-refresh' });
    expect(await useAuthStore.getState().refreshTokens()).toBe(false);
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockSecureStoreBacking.spinr_session_ended).toBe('1');
  });

  it('serializes logout after refresh and revokes the winning credential', async () => {
    useAuthStore.setState({ token: 'old-access', refreshToken: 'old-refresh' });
    mockSecureStoreBacking.refresh_token = 'old-refresh';
    let finish!: (value: unknown) => void;
    let entered!: () => void;
    const started = new Promise<void>(resolve => { entered = resolve; });
    mockPost.mockImplementation((url: string) => {
      if (url === '/auth/refresh') { entered(); return new Promise(resolve => { finish = resolve; }); }
      return Promise.resolve({ data: {} });
    });
    const renewal = useAuthStore.getState().refreshTokens();
    await started;
    const ending = useAuthStore.getState().logout();
    finish({ data: { token: 'new-access', refresh_token: 'new-refresh', access_expires_at: futureIso(900) } });
    await renewal;
    await ending;
    expect(mockPost).toHaveBeenCalledWith('/auth/logout', { refresh_token: 'new-refresh' });
    expect(mockSecureStoreBacking.refresh_token).toBeUndefined();
    expect(mockSecureStoreBacking.spinr_session_ended).toBe('1');
    expect(useAuthStore.getState().token).toBeNull();
  });

  it('does not let delayed go-offline cleanup wipe a new login', async () => {
    useAuthStore.setState({ token: 'old-access', driver: { id: 'driver-1' } as any });
    let finish!: () => void;
    (apiClient.put as jest.Mock).mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
    const ending = useAuthStore.getState().logout();
    await useAuthStore.getState().setTokens('new-login', 'new-login-refresh', 900);
    finish();
    await ending;
    expect(useAuthStore.getState().token).toBe('new-login');
    expect(mockSecureStoreBacking.refresh_token).toBe('new-login-refresh');
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('does not let a fast server-revoke cut off the go-offline PUT\'s bounded window', async () => {
    // Regression test for the bug found by the 2026-09-20 swarm-watch
    // drift-audit (issue #5591): the bounded window originally raced
    // goOffline against BOTH the timeout AND sessionWork, so a fast
    // server-side revoke (the common case) settled the wait before
    // goOffline had any real window — reintroducing the exact stuck
    // is_online bug this fix (PR #5530) was meant to close.
    useAuthStore.setState({ token: 'old-access', refreshToken: 'old-refresh', driver: { id: 'driver-1' } as any });
    mockSecureStoreBacking.refresh_token = 'old-refresh';
    mockPost.mockResolvedValue({ data: {} }); // server-side revoke resolves immediately — the fast path.
    let finishGoOffline!: () => void;
    (apiClient.put as jest.Mock).mockImplementationOnce(
      () => new Promise<void>((resolve) => { finishGoOffline = resolve; }),
    );

    let resolved = false;
    const ending = useAuthStore.getState().logout().then(() => { resolved = true; });

    // Let the fast server-side revoke (sessionWork) fully settle. Under the
    // bug this test guards against, sessionWork finishing here alone would
    // already resolve `ending`, even though the go-offline PUT is still
    // pending below.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    expect(mockPost).toHaveBeenCalledWith('/auth/logout', { refresh_token: 'old-refresh' });
    expect(resolved).toBe(false);

    finishGoOffline();
    await ending;
    expect(resolved).toBe(true);
    expect(apiClient.put).toHaveBeenCalledWith('/drivers/driver-1/status', { is_online: false });
  });

  it('revokes persisted background credentials even if foreground memory has no access token', async () => {
    Object.assign(mockSecureStoreBacking, { fg_access_token: 'background-access', refresh_token: 'background-refresh' });
    mockPost.mockResolvedValueOnce({ data: {} });
    await useAuthStore.getState().logout();
    expect(mockPost).toHaveBeenCalledWith('/auth/logout', { refresh_token: 'background-refresh' });
    expect(mockSecureStoreBacking.refresh_token).toBeUndefined();
  });
  it('retries with the background-rotated token instead of logging out', async () => {
    // Foreground holds a stale in-memory refresh token; the background task has
    // already rotated SecureStore forward to a fresh value.
    useAuthStore.setState({ refreshToken: 'stale-foreground' });
    mockSecureStoreBacking['refresh_token'] = 'fresh-from-background';

    mockPost.mockImplementation((url: string, body: { refresh_token: string }) => {
      if (url !== '/auth/refresh') throw new Error(`unexpected POST ${url}`);
      // The stale token (were it sent) is rejected; the background-rotated one
      // is accepted. Our fix should read storage first and send the fresh one.
      if (body.refresh_token === 'fresh-from-background') {
        return Promise.resolve({
          data: { token: 'access-new', refresh_token: 'refresh-next', access_expires_at: futureIso(900) },
          status: 200,
        });
      }
      return Promise.reject(make401());
    });

    const ok = await useAuthStore.getState().refreshTokens();

    expect(ok).toBe(true);
    expect(useAuthStore.getState().token).toBe('access-new');
    // Session preserved — no logout.
    expect(mockSecureStoreBacking['refresh_token']).toBe('refresh-next');
    // The interceptor sign-out must have been suppressed during the attempt and
    // reset afterwards.
    expect(mockSetSuppress).toHaveBeenCalledWith(true);
    expect(mockSetSuppress).toHaveBeenLastCalledWith(false);
  });

  it('detects a 401 rejected as a raw fetch Response (status on .status) and retries', async () => {
    // Pins the production shape: handleApiError rejects the /auth/refresh 401
    // with the raw Response, whose status is on `.status` (not `.response.status`).
    // If refreshTokens only read `.response.status` the retry branch would never
    // fire — the bug this test guards against.
    mockSecureStoreBacking['refresh_token'] = 'stale-shared';

    let posts = 0;
    mockPost.mockImplementation((url: string, body: { refresh_token: string }) => {
      if (url !== '/auth/refresh') throw new Error(`unexpected POST ${url}`);
      posts += 1;
      if (body.refresh_token === 'stale-shared') {
        // First attempt: reject with the RAW Response shape and have the other
        // context land its rotated token in storage right after.
        mockSecureStoreBacking['refresh_token'] = 'rotated-forward';
        return Promise.reject(make401RawResponse());
      }
      if (body.refresh_token === 'rotated-forward') {
        return Promise.resolve({
          data: { token: 'access-new', refresh_token: 'refresh-final', access_expires_at: futureIso(900) },
          status: 200,
        });
      }
      return Promise.reject(make401RawResponse());
    });

    const ok = await useAuthStore.getState().refreshTokens();

    expect(ok).toBe(true);
    expect(posts).toBe(2);
    expect(useAuthStore.getState().token).toBe('access-new');
  });

  it('logs out on a raw-Response 401 when the token is genuinely dead', async () => {
    useAuthStore.setState({ refreshToken: 'dead', token: 'old-access' });
    mockSecureStoreBacking['refresh_token'] = 'dead';

    mockPost.mockImplementation((url: string) => {
      if (url !== '/auth/refresh') throw new Error(`unexpected POST ${url}`);
      return Promise.reject(make401RawResponse());
    });

    const ok = await useAuthStore.getState().refreshTokens();

    expect(ok).toBe(false);
    expect(useAuthStore.getState().token).toBeNull();
    expect(mockSecureStoreBacking['refresh_token']).toBeUndefined();
  });

  it('recovers when the fresher token appears only on the second storage read', async () => {
    // Simultaneous-refresh window: at the moment of the 401 the winner has not
    // persisted its rotated token yet; it lands shortly after.
    mockSecureStoreBacking['refresh_token'] = 'shared-token';

    let firstRefreshSeen = false;
    mockPost.mockImplementation((url: string, body: { refresh_token: string }) => {
      if (url !== '/auth/refresh') throw new Error(`unexpected POST ${url}`);
      if (body.refresh_token === 'shared-token') {
        // Simulate the other context winning the race and persisting AFTER we
        // get our 401 back.
        firstRefreshSeen = true;
        setTimeout(() => {
          mockSecureStoreBacking['refresh_token'] = 'winner-rotated';
        }, 50);
        return Promise.reject(make401());
      }
      if (body.refresh_token === 'winner-rotated') {
        return Promise.resolve({
          data: { token: 'access-new', refresh_token: 'refresh-final', access_expires_at: futureIso(900) },
          status: 200,
        });
      }
      return Promise.reject(make401());
    });

    const ok = await useAuthStore.getState().refreshTokens();

    expect(firstRefreshSeen).toBe(true);
    expect(ok).toBe(true);
    expect(useAuthStore.getState().token).toBe('access-new');
  });

  it('logs out when the refresh token is genuinely dead (no rotation)', async () => {
    useAuthStore.setState({ refreshToken: 'dead-token', token: 'old-access' });
    mockSecureStoreBacking['refresh_token'] = 'dead-token';

    mockPost.mockImplementation((url: string) => {
      if (url !== '/auth/refresh') throw new Error(`unexpected POST ${url}`);
      return Promise.reject(make401());
    });

    const ok = await useAuthStore.getState().refreshTokens();

    expect(ok).toBe(false);
    expect(useAuthStore.getState().token).toBeNull();
    expect(mockSecureStoreBacking['refresh_token']).toBeUndefined();
    // Only one distinct token was ever tried (no spurious extra attempts).
    expect(mockPost).toHaveBeenCalledWith('/auth/refresh', { refresh_token: 'dead-token' });
    expect(mockSetSuppress).toHaveBeenLastCalledWith(false);
  });

  it('tears down an active session when no refresh token exists (auth limbo guard)', async () => {
    // The interceptor's G2 backstop no longer fires once a refresh was
    // attempted, so refreshTokens itself must clear an unrecoverable session
    // (active token/user but no refresh token anywhere).
    useAuthStore.setState({ token: 'orphan-access', refreshToken: null });
    // Nothing in storage either.

    const ok = await useAuthStore.getState().refreshTokens();
    // logout() is fire-and-forget (awaiting it inside refreshTokens can
    // deadlock behind the interceptor's in-flight refresh) — let it settle.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    expect(ok).toBe(false);
    expect(mockPost).not.toHaveBeenCalled();
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
  });

  it('is a no-op with no refresh token and no active session (cold start)', async () => {
    const SecureStore = require('expo-secure-store');
    // Call history persists across tests in this file — reset before asserting.
    (SecureStore.deleteItemAsync as jest.Mock).mockClear();

    const ok = await useAuthStore.getState().refreshTokens();
    await new Promise((r) => setTimeout(r, 0));

    expect(ok).toBe(false);
    expect(mockPost).not.toHaveBeenCalled();
    // No teardown side effects — nothing was cleared because nothing was set.
    expect(SecureStore.deleteItemAsync).not.toHaveBeenCalled();
  });

  it('keeps the session on a transient (5xx) refresh failure', async () => {
    useAuthStore.setState({ refreshToken: 'live-token', token: 'old-access' });
    mockSecureStoreBacking['refresh_token'] = 'live-token';

    mockPost.mockImplementation((url: string) => {
      if (url !== '/auth/refresh') throw new Error(`unexpected POST ${url}`);
      const err: any = new Error('service unavailable');
      err.response = { status: 503 };
      return Promise.reject(err);
    });

    const ok = await useAuthStore.getState().refreshTokens();

    expect(ok).toBe(false);
    // Transient — session and refresh token preserved.
    expect(mockSecureStoreBacking['refresh_token']).toBe('live-token');
  });
});
