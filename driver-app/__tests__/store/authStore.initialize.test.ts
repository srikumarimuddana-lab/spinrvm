/**
 * Pins the cold-start session-restoration fix in shared/store/authStore.ts.
 *
 * Background: authStore.setTokens() keeps the access token in memory only
 * and deletes any persisted `auth_token` from SecureStore. Before this
 * fix, initialize() only read `auth_token`, so every app restart left
 * the user logged out even though a valid refresh_token was still in
 * SecureStore. Users got sent back to the OTP screen every cold start.
 *
 * Fix: when there is no auth_token but there IS a refresh_token,
 * initialize() now calls refreshTokens() and hydrates /auth/me + /drivers/me
 * before falling through to the Firebase/logged-out branch.
 *
 * Code under test: shared/store/authStore.ts::initialize (refresh path)
 *
 * Note: imports the real shared authStore via a relative path to bypass
 * driver-app's `@shared/*` → `__mocks__/@shared/*` module-name mapper.
 */

import apiClient, {
  setInMemoryToken,
  setRefreshCallback,
  setCsrfToken,
} from '../../../shared/api/client';
import { registerLogoutCallback, useAuthStore } from '../../../shared/store/authStore';
import { appCache } from '../../../shared/cache';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

jest.mock('react-native', () => ({
  Platform: { OS: 'android' },
}));

// Firebase config must expose an `auth` object without `onAuthStateChanged`
// so initialize()'s Firebase branch is skipped (the else path sets the
// logged-out state synchronously).
jest.mock('../../../shared/config/firebaseConfig', () => ({
  auth: {},
}));

// firebase/auth is imported at the top of authStore. Stub named exports so
// the module loads — they only run from other actions (verifyOTP, logout).
jest.mock('firebase/auth', () => ({
  PhoneAuthProvider: { credential: jest.fn() },
  signInWithCredential: jest.fn(),
  signOut: jest.fn(),
}));

// SecureStore — in-memory backing. Variable prefixed `mock*` so the jest
// factory is allowed to reference it per the out-of-scope rule.
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

// Cache is a no-op for these tests — we only care about state transitions.
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

// Replace the api client. Use an auto-mock-style factory: jest.fn() calls
// inside the factory create fresh mocks that we then retrieve via import.
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

const mockGet = apiClient.get as jest.Mock;
const mockPost = apiClient.post as jest.Mock;
const mockSetInMemoryToken = setInMemoryToken as jest.Mock;
const mockSetRefreshCallback = setRefreshCallback as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  (Platform as any).OS = 'android';
  Object.keys(mockSecureStoreBacking).forEach((k) => delete mockSecureStoreBacking[k]);
  mockGet.mockReset();
  mockPost.mockReset();
  mockSetInMemoryToken.mockClear();
  mockSetRefreshCallback.mockClear();
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
    sessionRecoverable: false,
  });
});

describe('authStore.initialize — cold-start refresh-token restoration', () => {
  it('preserves the session when storage becomes unreadable during the 401 rotation check', async () => {
    mockSecureStoreBacking.refresh_token = 'persisted-refresh';
    useAuthStore.setState({ token: 'access', refreshToken: 'persisted-refresh' });
    (SecureStore.getItemAsync as jest.Mock)
      .mockResolvedValueOnce('persisted-refresh')
      .mockRejectedValueOnce(new Error('Keychain unavailable'));
    mockPost.mockRejectedValueOnce({ response: { status: 401 } });
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      await expect(useAuthStore.getState().refreshTokens()).resolves.toBe(false);
      expect(mockPost).toHaveBeenCalledTimes(1);
      expect(SecureStore.deleteItemAsync).not.toHaveBeenCalled();
      expect(useAuthStore.getState().token).toBe('access');
      expect(mockSecureStoreBacking.refresh_token).toBe('persisted-refresh');
      expect(errorLog).toHaveBeenCalled();
    } finally {
      errorLog.mockRestore();
    }
  });

  it('keeps cold start recoverable when the final credential reread is unavailable', async () => {
    mockSecureStoreBacking.refresh_token = 'persisted-refresh';
    (SecureStore.getItemAsync as jest.Mock)
      .mockResolvedValueOnce('persisted-refresh')
      .mockResolvedValueOnce('persisted-refresh')
      .mockRejectedValueOnce(new Error('Keychain unavailable'));
    mockPost.mockRejectedValueOnce({ response: { status: 503 } });
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      await useAuthStore.getState().initialize();
      expect(SecureStore.getItemAsync).toHaveBeenCalledTimes(3);
      expect(SecureStore.deleteItemAsync).not.toHaveBeenCalled();
      expect(mockSecureStoreBacking.refresh_token).toBe('persisted-refresh');
      expect(useAuthStore.getState()).toMatchObject({
        token: null, isInitialized: true, isLoading: false, sessionRecoverable: true,
      });
      expect(errorLog).toHaveBeenCalled();
    } finally {
      errorLog.mockRestore();
    }
  });

  it.each(['ios', 'android'])('preserves credentials when %s secure storage is unavailable, then retries', async (os) => {
    (Platform as any).OS = os;
    mockSecureStoreBacking.refresh_token = 'persisted-refresh';
    mockSecureStoreBacking.fg_access_token = 'previous-access';
    (SecureStore.getItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain unavailable'));
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});

    await useAuthStore.getState().initialize();

    expect(mockSecureStoreBacking.refresh_token).toBe('persisted-refresh');
    expect(SecureStore.deleteItemAsync).not.toHaveBeenCalled();
    expect(mockSecureStoreBacking.spinr_session_ended).toBeUndefined();
    expect(mockPost).not.toHaveBeenCalled();
    expect(useAuthStore.getState()).toMatchObject({ isInitialized: true, isLoading: false, sessionRecoverable: true });
    expect(errorLog).toHaveBeenCalled();

    mockPost.mockResolvedValueOnce({ data: { token: 'access', refresh_token: 'rotated', expires_in: 900 } });
    mockGet.mockResolvedValueOnce({ data: { id: 'user', is_driver: false } });
    await useAuthStore.getState().initialize();
    expect(useAuthStore.getState()).toMatchObject({ token: 'access', sessionRecoverable: false });
    errorLog.mockRestore();
  });

  it('keeps an active session when secure storage fails during refresh', async () => {
    useAuthStore.setState({ token: 'access', refreshToken: 'possibly-stale' });
    (SecureStore.getItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain unavailable'));
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});
    expect(await useAuthStore.getState().refreshTokens()).toBe(false);
    expect(mockPost).not.toHaveBeenCalled();
    expect(SecureStore.deleteItemAsync).not.toHaveBeenCalled();
    expect(useAuthStore.getState().token).toBe('access');
    errorLog.mockRestore();
  });

  it('exits recovery when a successful read confirms there is no token', async () => {
    useAuthStore.setState({ sessionRecoverable: true });
    await useAuthStore.getState().initialize();
    expect(useAuthStore.getState().sessionRecoverable).toBe(false);
  });

  it('restores the session via refresh_token when no auth_token is stored', async () => {
    // Post-login-and-force-close state: setTokens() already deleted
    // auth_token but persisted refresh_token. Nothing is in memory yet.
    mockSecureStoreBacking['refresh_token'] = 'persisted-refresh-abc';
    mockSecureStoreBacking['token_expires_at'] = String(Date.now() + 900_000);

    mockPost.mockImplementation((url: string) => {
      if (url === '/auth/refresh') {
        return Promise.resolve({
          data: {
            token: 'new-access-xyz',
            refresh_token: 'new-refresh-abc',
            expires_in: 900,
          },
          status: 200,
        });
      }
      throw new Error(`unexpected POST ${url}`);
    });

    mockGet.mockImplementation((url: string) => {
      if (url === '/auth/me') {
        return Promise.resolve({
          data: {
            id: 'u-1',
            phone: '+13065550100',
            role: 'driver',
            is_driver: true,
            profile_complete: true,
            created_at: '2026-04-01T00:00:00Z',
            driver_onboarding_status: 'verified',
          },
          status: 200,
        });
      }
      if (url === '/drivers/me') {
        return Promise.resolve({
          data: {
            id: 'd-1',
            user_id: 'u-1',
            name: 'Test Driver',
            phone: '+13065550100',
            vehicle_type_id: 'sedan',
            vehicle_make: 'Toyota',
            vehicle_model: 'Camry',
            vehicle_color: 'White',
            license_plate: 'ABC-123',
            rating: 5,
            total_rides: 0,
            is_online: false,
            is_available: false,
          },
          status: 200,
        });
      }
      throw new Error(`unexpected GET ${url}`);
    });

    await useAuthStore.getState().initialize();

    const state = useAuthStore.getState();
    expect(state.isInitialized).toBe(true);
    expect(state.isLoading).toBe(false);
    expect(state.token).toBe('new-access-xyz');
    expect(state.user?.id).toBe('u-1');
    expect(state.driver?.id).toBe('d-1');

    // The refresh endpoint must have been called with the persisted token.
    expect(mockPost).toHaveBeenCalledWith('/auth/refresh', {
      refresh_token: 'persisted-refresh-abc',
    });
    // The fresh access token must be registered with the api client so
    // subsequent requests carry the Authorization header.
    expect(mockSetInMemoryToken).toHaveBeenCalledWith('new-access-xyz');
    // New refresh token persisted by setTokens().
    expect(mockSecureStoreBacking['refresh_token']).toBe('new-refresh-abc');
  });

  it('stays logged-out when neither auth_token nor refresh_token exist', async () => {
    await useAuthStore.getState().initialize();

    const state = useAuthStore.getState();
    expect(state.isInitialized).toBe(true);
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockGet).not.toHaveBeenCalled();
  });

  it('falls through to logged-out when the persisted refresh_token is rejected', async () => {
    mockSecureStoreBacking['refresh_token'] = 'expired-refresh';

    mockPost.mockImplementation((url: string) => {
      if (url === '/auth/refresh') {
        const err: any = new Error('refresh token expired');
        err.response = { status: 401, data: { detail: 'expired' } };
        return Promise.reject(err);
      }
      throw new Error(`unexpected POST ${url}`);
    });

    await useAuthStore.getState().initialize();

    const state = useAuthStore.getState();
    expect(state.isInitialized).toBe(true);
    expect(state.token).toBeNull();
    expect(state.user).toBeNull();
    // refreshTokens()'s catch calls logout() which clears the stale token.
    expect(mockSecureStoreBacking['refresh_token']).toBeUndefined();
  });

  it('settles initialization when rejected-token cleanup cannot persist the logout marker', async () => {
    mockSecureStoreBacking['refresh_token'] = 'expired-refresh';
    mockPost.mockImplementation((url: string) => {
      if (url === '/auth/refresh') {
        const err: any = new Error('refresh token expired');
        err.response = { status: 401, data: { detail: 'expired' } };
        return Promise.reject(err);
      }
      throw new Error(`unexpected POST ${url}`);
    });
    // No writes occur before definitive-401 logout, so this rejects the
    // spinr_session_ended marker while leaving all delete operations real.
    (SecureStore.setItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain unavailable'));
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});

    // Previously asserted `.rejects.toThrow('Unable to save your session
    // securely')`. That rejection originated in logout()'s marker write and
    // propagated out through refreshTokens() -> initialize(). logout() now
    // reports instead of rejecting, so this path settles cleanly. The
    // assertions below are this test's real subject and are unchanged:
    // initialization is settled, the session is fully wiped, and the failure
    // is still surfaced.
    // Token-persistence failures are also handled by refreshTokens(), which
    // returns false so initialization can offer session recovery.
    await expect(useAuthStore.getState().initialize()).resolves.toBeUndefined();

    expect(useAuthStore.getState()).toMatchObject({
      token: null,
      refreshToken: null,
      user: null,
      isInitialized: true,
      isLoading: false,
      sessionRecoverable: false,
    });
    expect(mockSecureStoreBacking['refresh_token']).toBeUndefined();
    expect(errorLog).toHaveBeenCalled();
    errorLog.mockRestore();
  });
});

describe('session-ended marker + full token wipe', () => {
  it('resolves and reports — never rejects — when persisting the logout marker fails', async () => {
    // Contract: a failed marker write must stay VISIBLE (console.error +
    // captureMessage) but must not reject.
    //
    // This previously asserted `.rejects.toThrow()`. Rejecting protected
    // nothing — the local teardown and _runLogoutCallbacks() (which tears down
    // driver location) run either way via the finally — while ~7 callers do
    // `await logout(); router.replace('/login')` with no catch, so the throw
    // skipped the navigation and stranded the user on a screen whose store had
    // just been nulled. See docs/change-log/2026-09-13-logout-must-not-reject.md.
    useAuthStore.setState({ token: 'access', refreshToken: 'refresh', user: { id: 'user' } as any });
    (SecureStore.setItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain unavailable'));
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});

    await expect(
      useAuthStore.getState().logout({ revokeServerSession: false }),
    ).resolves.toBeUndefined();

    expect(useAuthStore.getState()).toMatchObject({ token: null, refreshToken: null, user: null });
    // The failure is still surfaced — silence here would be the real defect.
    expect(errorLog).toHaveBeenCalled();
    errorLog.mockRestore();
  });

  it.each(['ios', 'android'].flatMap((os) =>
    ['refresh_token', 'token_expires_at', 'fg_access_token'].map((key) => [os, key]),
  ))('rejects a failed %s %s write before publishing access or CSRF', async (os, failedKey) => {
    (Platform as any).OS = os;
    (SecureStore.setItemAsync as jest.Mock).mockImplementation((key: string, value: string) => {
      if (key === failedKey) return Promise.reject(new Error('Keychain unavailable'));
      mockSecureStoreBacking[key] = value;
      return Promise.resolve();
    });
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      await expect(useAuthStore.getState().setTokens('new-access', 'new-refresh', 900, 'new-csrf')).rejects.toThrow();
      expect(mockSetInMemoryToken).not.toHaveBeenCalled();
      expect(setCsrfToken).not.toHaveBeenCalled();
      expect(useAuthStore.getState()).toMatchObject({ token: null, refreshToken: null });
      expect(mockSecureStoreBacking[failedKey]).toBeUndefined();
      expect(errorLog).toHaveBeenCalled();
    } finally {
      (SecureStore.setItemAsync as jest.Mock).mockImplementation((key: string, value: string) => {
        mockSecureStoreBacking[key] = value;
        return Promise.resolve();
      });
      errorLog.mockRestore();
    }
  });

  it.each([false, true])('runs logout callbacks after a failed marker write (cache failure: %s)', async (cacheFails) => {
    useAuthStore.setState({ token: 'access', refreshToken: 'refresh', user: { id: 'user' } as any });
    (SecureStore.setItemAsync as jest.Mock).mockRejectedValueOnce(new Error('Keychain unavailable'));
    if (cacheFails) (appCache.clearUserCache as jest.Mock).mockRejectedValueOnce(new Error('Cache unavailable'));
    const teardown = jest.fn();
    const unregister = registerLogoutCallback(teardown);
    const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});
    try {
      const pending = useAuthStore.getState().logout({ revokeServerSession: false });
      if (cacheFails) await expect(pending).rejects.toThrow('Cache unavailable');
      else await expect(pending).resolves.toBeUndefined();
      expect(teardown).toHaveBeenCalledTimes(1);
      expect(useAuthStore.getState()).toMatchObject({ user: null, token: null, refreshToken: null });
    } finally {
      unregister();
      errorLog.mockRestore();
    }
  });

  it('waits for refresh-token persistence before publishing access', async () => {
    let finishWrite!: () => void;
    (SecureStore.setItemAsync as jest.Mock).mockImplementationOnce((key: string, value: string) =>
      new Promise<void>((resolve) => { finishWrite = () => { mockSecureStoreBacking[key] = value; resolve(); }; }),
    );
    const pending = useAuthStore.getState().setTokens('new-access', 'new-refresh', 900);
    await Promise.resolve();
    await Promise.resolve();
    expect(mockSetInMemoryToken).not.toHaveBeenCalled();
    finishWrite();
    await pending;
    expect(mockSecureStoreBacking.refresh_token).toBe('new-refresh');
    expect(mockSetInMemoryToken).toHaveBeenCalledWith('new-access');
  });

  it('clearAuthStorage removes the background task credentials and records the sign-out', async () => {
    // Regression: clearAuthStorage() claimed to wipe "every auth artifact" but
    // left fg_access_token / bg_access_token* behind, and never ran the logout
    // callbacks. A session that ended by *expiry* therefore kept the headless
    // background-location task both recording and uploading — the reported bug,
    // on a different trigger than explicit sign-out.
    mockSecureStoreBacking['auth_token'] = 'stale-access';
    mockSecureStoreBacking['fg_access_token'] = 'stale-fg-access';
    mockSecureStoreBacking['bg_access_token'] = 'stale-bg-access';
    mockSecureStoreBacking['bg_access_token_expires'] = String(Date.now() + 600_000);
    mockSecureStoreBacking['token_expires_at'] = String(Date.now() + 600_000);
    // No refresh_token → initialize() takes the "no valid stored token" branch.

    await useAuthStore.getState().initialize();

    expect(mockSecureStoreBacking['auth_token']).toBeUndefined();
    expect(mockSecureStoreBacking['fg_access_token']).toBeUndefined();
    expect(mockSecureStoreBacking['bg_access_token']).toBeUndefined();
    expect(mockSecureStoreBacking['bg_access_token_expires']).toBeUndefined();
    expect(mockSecureStoreBacking['token_expires_at']).toBeUndefined();
    // Positive evidence for the headless contexts, which cannot read this store.
    expect(mockSecureStoreBacking['spinr_session_ended']).toBe('1');
  });

  it('logout records the marker so headless tasks can observe the sign-out', async () => {
    useAuthStore.setState({ token: 'live-access', user: { id: 'u1' } as never });
    mockPost.mockResolvedValue({ data: { success: true }, status: 200 });

    await useAuthStore.getState().logout();

    expect(mockSecureStoreBacking['spinr_session_ended']).toBe('1');
  });

  it('setTokens clears the marker so a new sign-in resumes tracking', async () => {
    mockSecureStoreBacking['spinr_session_ended'] = '1';

    await useAuthStore.getState().setTokens('access', 'refresh', 900);

    expect(mockSecureStoreBacking['spinr_session_ended']).toBeUndefined();
    expect(mockSecureStoreBacking['refresh_token']).toBe('refresh');
    expect(mockSecureStoreBacking['fg_access_token']).toBe('access');
  });
});
