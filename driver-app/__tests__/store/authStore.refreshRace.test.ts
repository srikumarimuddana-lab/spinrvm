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

import apiClient, {
  setSuppressRefreshSignOut,
} from '../../../shared/api/client';
import { useAuthStore } from '../../../shared/store/authStore';

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

beforeEach(() => {
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
          data: { token: 'access-new', refresh_token: 'refresh-next', expires_in: 900 },
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
          data: { token: 'access-new', refresh_token: 'refresh-final', expires_in: 900 },
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
          data: { token: 'access-new', refresh_token: 'refresh-final', expires_in: 900 },
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
