/**
 * Session material must not outlive the session — including the driver app's
 * background-task keys.
 *
 * The driver app's headless location task authorises its uploads from
 * SecureStore `bg_access_token` (utils/backgroundLocation.ts::
 * getBackgroundAuthToken), which only stopBackgroundLocation() ever deleted — and
 * that runs solely from the go-offline branch of useDriverDashboard.toggleOnline.
 * driver-app registers no logout callback, so a driver who signed out WITHOUT
 * first going offline left a valid access token in storage and the headless task
 * kept POSTing /drivers/location-batch with it for the rest of the token's TTL.
 * logout() does not bump token_version, so the backend accepted those uploads.
 *
 * Two leaks, not one: `logout()` cleared 4 of the 6 session keys and
 * `clearAuthStorage()` — the no-valid-session path in initialize() — cleared only
 * 3, leaving BOTH access tokens behind. Both now iterate one canonical
 * AUTH_STORAGE_KEYS list, so a key added in future cannot be covered by one path
 * and missed by the other.
 *
 * The second half of the leak — the NATIVE task continuing to run — is closed by
 * a registerLogoutCallback in driver-app/app/_layout.tsx. That mechanism had no
 * direct test either, so its contract is pinned at the bottom of this file.
 *
 * Code under test: shared/store/authStore.ts::logout / ::clearAuthStorage /
 * ::registerLogoutCallback
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

import { useAuthStore } from '../../../shared/store/authStore';

/** Every key that holds session material. Mirrors AUTH_STORAGE_KEYS. */
const SESSION_KEYS = [
  'auth_token',
  'fg_access_token',
  'bg_access_token',
  'bg_access_token_expires',
  'refresh_token',
  'token_expires_at',
];

/** Populate storage as a signed-in driver who went online at least once. */
const seedSignedInDriverStorage = (): void => {
  mockSecureStoreBacking['auth_token'] = 'legacy-access';
  mockSecureStoreBacking['fg_access_token'] = 'foreground-access';
  mockSecureStoreBacking['bg_access_token'] = 'background-cached-access';
  mockSecureStoreBacking['bg_access_token_expires'] = String(Date.now() + 900_000);
  mockSecureStoreBacking['refresh_token'] = 'refresh-abc';
  mockSecureStoreBacking['token_expires_at'] = String(Date.now() + 900_000);
  // Not session material — must survive, so the assertions can't pass by
  // accidentally nuking the whole store.
  mockSecureStoreBacking['spinr_bg_geofence_centre'] = '{"lat":52.1,"lng":-106.6}';
};

beforeEach(() => {
  Object.keys(mockSecureStoreBacking).forEach((k) => delete mockSecureStoreBacking[k]);
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

describe('authStore.logout — clears every session key', () => {
  it('leaves no access token behind for the headless location task', async () => {
    seedSignedInDriverStorage();

    await useAuthStore.getState().logout();

    for (const key of SESSION_KEYS) {
      expect(mockSecureStoreBacking[key]).toBeUndefined();
    }
  });

  it('specifically clears bg_access_token — the key the background task reads', async () => {
    // Called out separately because this is the one that leaked: it is written by
    // getBackgroundAuthToken, not by setTokens, so it was absent from logout's
    // hand-written delete list.
    seedSignedInDriverStorage();

    await useAuthStore.getState().logout();

    expect(mockSecureStoreBacking['bg_access_token']).toBeUndefined();
    expect(mockSecureStoreBacking['bg_access_token_expires']).toBeUndefined();
  });

  it('does not wipe unrelated device-local state', async () => {
    seedSignedInDriverStorage();

    await useAuthStore.getState().logout();

    // The geofence centre is operational state, not session material.
    expect(mockSecureStoreBacking['spinr_bg_geofence_centre']).toBe('{"lat":52.1,"lng":-106.6}');
  });

  it('is safe when nothing is stored', async () => {
    await expect(useAuthStore.getState().logout()).resolves.toBeUndefined();
  });
});

describe('authStore.initialize — the no-valid-session path clears the same keys', () => {
  it('clears both access tokens when there is no refresh token to recover with', async () => {
    // clearAuthStorage() previously cleared only auth_token / refresh_token /
    // token_expires_at, so a cold start with no refresh token left
    // fg_access_token AND bg_access_token usable by the headless task.
    seedSignedInDriverStorage();
    delete mockSecureStoreBacking['refresh_token']; // forces the logged-out branch

    await useAuthStore.getState().initialize();

    for (const key of SESSION_KEYS) {
      expect(mockSecureStoreBacking[key]).toBeUndefined();
    }
    expect(useAuthStore.getState().isInitialized).toBe(true);
    expect(useAuthStore.getState().token).toBeNull();
  });
});

// ── The logout-callback mechanism ────────────────────────────────────────────
// driver-app/app/_layout.tsx relies on registerLogoutCallback to tear down the
// native background-location task and the recovery geofence on sign-out. That
// mechanism had NO direct test — the only references anywhere were jest mocks in
// rider-app suites — so the contract it depends on was unverified.
describe('registerLogoutCallback — the contract _layout relies on', () => {
  it('runs registered callbacks during logout', async () => {
    const { registerLogoutCallback } = require('../../../shared/store/authStore');
    const teardown = jest.fn();
    const unregister = registerLogoutCallback(teardown);

    try {
      await useAuthStore.getState().logout();
      expect(teardown).toHaveBeenCalledTimes(1);
    } finally {
      unregister();
    }
  });

  it('awaits async callbacks before logout resolves', async () => {
    const { registerLogoutCallback } = require('../../../shared/store/authStore');
    let finished = false;
    const unregister = registerLogoutCallback(async () => {
      await new Promise((r) => setTimeout(r, 10));
      finished = true;
    });

    try {
      await useAuthStore.getState().logout();
      // If logout did not await, `finished` would still be false here and the
      // native teardown could race the next sign-in.
      expect(finished).toBe(true);
    } finally {
      unregister();
    }
  });

  it('a throwing callback cannot block sign-out', async () => {
    // A native teardown failure (stopLocationUpdatesAsync rejecting) must never
    // trap the user in a signed-in-looking state.
    const { registerLogoutCallback } = require('../../../shared/store/authStore');
    const unregister = registerLogoutCallback(() => {
      throw new Error('stopLocationUpdatesAsync failed');
    });

    try {
      seedSignedInDriverStorage();
      await expect(useAuthStore.getState().logout()).resolves.toBeUndefined();
      // Storage is still cleared — the callback runs last, so its failure cannot
      // leave session material behind.
      for (const key of SESSION_KEYS) {
        expect(mockSecureStoreBacking[key]).toBeUndefined();
      }
    } finally {
      unregister();
    }
  });

  it('stops running a callback once unregistered', async () => {
    const { registerLogoutCallback } = require('../../../shared/store/authStore');
    const teardown = jest.fn();
    registerLogoutCallback(teardown)(); // register then immediately unregister

    await useAuthStore.getState().logout();

    expect(teardown).not.toHaveBeenCalled();
  });
});
