/**
 * Pins access-token expiry derivation in shared/store/authStore.ts.
 *
 * Background: the backend's /auth/refresh response carried `access_expires_at`
 * but NOT `expires_in`, while refreshTokens() destructured `expires_in` and
 * setTokens() computed `Date.now() + expiresIn * 1000`. That evaluated to NaN
 * after the first refresh of every session, with two consequences:
 *
 *   1. `tokenExpiresAt` read as falsy, so ensureFreshToken()'s
 *      `!tokenExpiresAt` guard (shared/api/client.ts) returned early and the
 *      proactive 2-minute-before-expiry refresh never ran again. Every expiry
 *      became a reactive 401 burst instead of a quiet background rotation.
 *   2. SecureStore `token_expires_at` was written as the string "NaN". The
 *      driver app's headless location task parses that value to decide whether
 *      the persisted access token is still fresh
 *      (utils/backgroundLocation.ts::getBackgroundAuthToken), so
 *      `parseInt("NaN")` silently disabled headless location-batch uploads —
 *      the breadcrumbs that settle billed distance and the SGI insurance-period
 *      audit.
 *
 * The backend now sends `expires_in` (see backend/routes/auth.py::RefreshResponse
 * and backend/tests/test_p1_token_refresh.py). These tests pin the CLIENT side
 * so a future response-shape drift cannot silently reintroduce the same failure:
 * the TTL is derived from whichever field is present, and a non-finite value can
 * never reach state or storage.
 *
 * Code under test: shared/store/authStore.ts::setTokens / ::refreshTokens
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

import apiClient from '../../../shared/api/client';
import { useAuthStore } from '../../../shared/store/authStore';

const mockPost = apiClient.post as jest.Mock;

const FIFTEEN_MIN_MS = 15 * 60 * 1000;

/** Replicates how backgroundLocation.getBackgroundAuthToken reads the value. */
const readPersistedExpiryAsBackgroundTaskWould = (): number =>
  parseInt(mockSecureStoreBacking['token_expires_at'], 10);

beforeEach(() => {
  Object.keys(mockSecureStoreBacking).forEach((k) => delete mockSecureStoreBacking[k]);
  mockPost.mockReset();
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

describe('authStore.setTokens — expiry can never be non-finite', () => {
  it.each([
    ['undefined', undefined],
    ['NaN', NaN],
    ['null', null],
    ['a non-numeric string', '900'],
    ['zero', 0],
    ['a negative TTL', -60],
  ])('defaults to 900s when the TTL is %s', async (_label, badTtl) => {
    await useAuthStore.getState().setTokens('access-tok', 'refresh-tok', badTtl as number);

    const { tokenExpiresAt } = useAuthStore.getState();
    expect(Number.isFinite(tokenExpiresAt)).toBe(true);
    expect(tokenExpiresAt).toBeGreaterThan(Date.now());
    // 900s default, allowing generous slack for slow CI.
    expect(tokenExpiresAt! - Date.now()).toBeGreaterThan(FIFTEEN_MIN_MS - 10_000);

    // The persisted copy must be parseable — this is the value the driver app's
    // headless location task reads to authorise batch uploads.
    expect(mockSecureStoreBacking['token_expires_at']).not.toBe('NaN');
    expect(Number.isFinite(readPersistedExpiryAsBackgroundTaskWould())).toBe(true);
  });

  it('uses a valid TTL as given', async () => {
    await useAuthStore.getState().setTokens('access-tok', 'refresh-tok', 300);

    const { tokenExpiresAt } = useAuthStore.getState();
    const remainingMs = tokenExpiresAt! - Date.now();
    expect(remainingMs).toBeGreaterThan(295_000);
    expect(remainingMs).toBeLessThanOrEqual(300_000);
  });

  it('warns loudly (not only in __DEV__) when it has to substitute a default', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      await useAuthStore.getState().setTokens('access-tok', 'refresh-tok', undefined as unknown as number);
      expect(warn).toHaveBeenCalledWith(expect.stringContaining('Unusable access-token TTL'));
    } finally {
      warn.mockRestore();
    }
  });

  it('does not warn on a valid TTL', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      await useAuthStore.getState().setTokens('access-tok', 'refresh-tok', 900);
      expect(warn).not.toHaveBeenCalledWith(expect.stringContaining('Unusable access-token TTL'));
    } finally {
      warn.mockRestore();
    }
  });
});

describe('authStore.refreshTokens — TTL derivation from the server response', () => {
  const primeRefreshToken = () => {
    mockSecureStoreBacking['refresh_token'] = 'current-refresh';
  };

  it('uses expires_in when the server sends it', async () => {
    primeRefreshToken();
    mockPost.mockResolvedValue({
      data: {
        token: 'new-access',
        refresh_token: 'next-refresh',
        expires_in: 900,
        access_expires_at: new Date(Date.now() + FIFTEEN_MIN_MS).toISOString(),
      },
    });

    await expect(useAuthStore.getState().refreshTokens()).resolves.toBe(true);

    const remainingMs = useAuthStore.getState().tokenExpiresAt! - Date.now();
    expect(remainingMs).toBeGreaterThan(FIFTEEN_MIN_MS - 10_000);
    expect(remainingMs).toBeLessThanOrEqual(FIFTEEN_MIN_MS);
  });

  it('REGRESSION: derives the TTL from access_expires_at when expires_in is absent', async () => {
    // This is verbatim the shape the backend returned before 2026-07-29, and the
    // shape an installed app still sees when talking to an older backend. It
    // previously produced tokenExpiresAt === NaN.
    primeRefreshToken();
    mockPost.mockResolvedValue({
      data: {
        token: 'new-access',
        refresh_token: 'next-refresh',
        access_expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
        refresh_expires_at: new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString(),
      },
    });

    await expect(useAuthStore.getState().refreshTokens()).resolves.toBe(true);

    const { tokenExpiresAt } = useAuthStore.getState();
    expect(Number.isFinite(tokenExpiresAt)).toBe(true);
    const remainingMs = tokenExpiresAt! - Date.now();
    // Derived from access_expires_at (~10 min), NOT the 900s blanket default.
    expect(remainingMs).toBeGreaterThan(9 * 60 * 1000);
    expect(remainingMs).toBeLessThanOrEqual(10 * 60 * 1000);

    // And the value the headless background task reads must be parseable.
    expect(mockSecureStoreBacking['token_expires_at']).not.toBe('NaN');
    expect(Number.isFinite(readPersistedExpiryAsBackgroundTaskWould())).toBe(true);
  });

  it('falls back to the default when neither field is usable', async () => {
    primeRefreshToken();
    mockPost.mockResolvedValue({
      data: { token: 'new-access', refresh_token: 'next-refresh' },
    });

    await expect(useAuthStore.getState().refreshTokens()).resolves.toBe(true);

    const remainingMs = useAuthStore.getState().tokenExpiresAt! - Date.now();
    expect(remainingMs).toBeGreaterThan(FIFTEEN_MIN_MS - 10_000);
  });

  it('ignores an access_expires_at that is already in the past', async () => {
    // Clock skew or a stale/replayed response. Deriving a negative TTL would
    // leave tokenExpiresAt in the past, which makes ensureFreshToken refresh on
    // every single tick — a refresh storm. Fall back to the default instead.
    primeRefreshToken();
    mockPost.mockResolvedValue({
      data: {
        token: 'new-access',
        refresh_token: 'next-refresh',
        access_expires_at: new Date(Date.now() - 60_000).toISOString(),
      },
    });

    await expect(useAuthStore.getState().refreshTokens()).resolves.toBe(true);

    const { tokenExpiresAt } = useAuthStore.getState();
    expect(tokenExpiresAt).toBeGreaterThan(Date.now());
    expect(tokenExpiresAt! - Date.now()).toBeGreaterThan(FIFTEEN_MIN_MS - 10_000);
  });

  it('ignores an unparseable access_expires_at', async () => {
    primeRefreshToken();
    mockPost.mockResolvedValue({
      data: {
        token: 'new-access',
        refresh_token: 'next-refresh',
        access_expires_at: 'not-a-timestamp',
      },
    });

    await expect(useAuthStore.getState().refreshTokens()).resolves.toBe(true);

    const { tokenExpiresAt } = useAuthStore.getState();
    expect(Number.isFinite(tokenExpiresAt)).toBe(true);
    expect(tokenExpiresAt).toBeGreaterThan(Date.now());
  });
});
