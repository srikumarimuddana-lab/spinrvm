import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { auth } from '../config/firebaseConfig';
import { PhoneAuthProvider, signInWithCredential, signOut, User as FirebaseUser } from 'firebase/auth';
import api, { setCsrfToken, setInMemoryToken, setRefreshCallback } from '../api/client';
import { appCache, CACHE_KEYS, CACHE_CONFIG } from '../cache';

// Narrows an unknown caught value to an Axios-style error shape so callers
// can safely access `.response.status` and `.response.data.detail` without
// casting to `any`.
function isApiError(e: unknown): e is {
  response?: { status?: number; data?: { detail?: string } };
  message?: string;
} {
  return typeof e === 'object' && e !== null;
}

// React Native's FormData.append accepts a file descriptor object that the
// native fetch serialises to multipart/form-data. The standard TS types only
// expose Blob/string, so we name the RN extension to avoid `as any`.
type RNFormFile = { uri: string; name: string; type: string };

// Wipe every auth artifact from local storage. Called whenever initialize
// lands in a "no valid session" state so stale refresh tokens from a past
// session can never wedge the next cold start. Previously the Firebase
// timeout + no-Firebase-user paths left `refresh_token` in SecureStore,
// which is why users had to uninstall to recover from a failed refresh
// that returned anything other than 401 (5xx, timeout, "Network request
// failed"). Safe to call even when nothing is stored — deleteItem is a
// no-op on missing keys.
async function clearAuthStorage(): Promise<void> {
  try {
    if (Platform.OS === 'web') {
      sessionStorage.removeItem('auth_token');
      sessionStorage.removeItem('refresh_token');
      sessionStorage.removeItem('token_expires_at');
      return;
    }
    await SecureStore.deleteItemAsync('auth_token');
    await SecureStore.deleteItemAsync('refresh_token');
    await SecureStore.deleteItemAsync('token_expires_at');
  } catch (e) {
    // Best-effort — never let a storage error block the login screen.
    if (__DEV__) console.log('[Auth] clearAuthStorage failed:', e);
  }
}

// Platform-safe secure storage
// Web uses sessionStorage (clears on tab close) instead of localStorage
// to reduce token exposure in browser storage.
const storage = {
  async getItem(key: string): Promise<string | null> {
    try {
      if (Platform.OS === 'web') {
        return sessionStorage.getItem(key);
      }
      return await SecureStore.getItemAsync(key);
    } catch (e) {
      console.log('Storage getItem error:', e);
      return null;
    }
  },
  async setItem(key: string, value: string): Promise<void> {
    try {
      if (Platform.OS === 'web') {
        sessionStorage.setItem(key, value);
        return;
      }
      return await SecureStore.setItemAsync(key, value);
    } catch (e) {
      console.log('Storage setItem error:', e);
    }
  },
  async deleteItem(key: string): Promise<void> {
    try {
      if (Platform.OS === 'web') {
        sessionStorage.removeItem(key);
        return;
      }
      return await SecureStore.deleteItemAsync(key);
    } catch (e) {
      console.log('Storage deleteItem error:', e);
    }
  },
};

export interface Driver {
  id: string;
  user_id: string;
  name: string;
  phone: string;
  vehicle_type_id: string;
  vehicle_make: string;
  vehicle_model: string;
  vehicle_color: string;
  vehicle_year?: number;
  vehicle_vin?: string;
  license_plate: string;
  rating: number;
  total_rides: number;
  is_online: boolean;
  is_available: boolean;
  is_verified?: boolean;
  license_expiry_date?: string;
  insurance_expiry_date?: string;
  background_check_expiry_date?: string;
  vehicle_inspection_expiry_date?: string;
  is_wav?: boolean;
  [key: string]: unknown;
}

export type DriverOnboardingStatus =
  | 'profile_incomplete'
  | 'vehicle_required'
  | 'documents_required'
  | 'documents_rejected'
  | 'documents_expired'
  | 'pending_review'
  | 'verified'
  | 'suspended';

export interface User {
  id: string;
  phone: string;
  first_name?: string;
  last_name?: string;
  email?: string;
  gender?: string;
  city?: string;
  role: string;
  created_at: string;
  profile_complete: boolean;
  is_driver?: boolean;
  profile_image?: string;  // Base64 data URI
  profile_image_status?: 'pending_review' | 'approved' | 'rejected' | null;
  rating?: number;
  total_rides?: number;
  // Driver onboarding state machine (computed server-side on every /auth/me).
  // Null for riders. Clients should route on this rather than profile_complete.
  driver_onboarding_status?: DriverOnboardingStatus | null;
  driver_onboarding_detail?: string | null;
  driver_onboarding_next_screen?: string | null;
}

interface RefreshTokenResponse {
  token: string;
  refresh_token: string;
  expires_in: number;
  csrf_token?: string | null;
}

// Payload accepted by POST /drivers/register — all fields are optional;
// the backend populates required fields from the authenticated user.
export type DriverRegistrationPayload = Record<string, unknown>;

interface AuthState {
  user: User | null;
  driver: Driver | null;
  isDriverMode: boolean;
  token: string | null;
  refreshToken: string | null;
  tokenExpiresAt: number | null;   // Unix ms — when the access token expires
  isLoading: boolean;
  isInitialized: boolean;
  error: string | null;

  // Actions
  initialize: () => Promise<void>;
  verifyOTP: (verificationId: string, code: string) => Promise<void>;
  setTokens: (token: string, refreshToken: string, expiresIn: number, csrfToken?: string | null) => Promise<void>;
  refreshTokens: () => Promise<boolean>;
  createProfile: (data: {
    first_name: string;
    last_name: string;
    email: string;
    gender: string;
    city?: string;
    service_area_id?: string;
  }) => Promise<void>;
  fetchDriverProfile: () => Promise<void>;
  refreshProfile: () => Promise<void>;
  registerDriver: (data: DriverRegistrationPayload) => Promise<void>;
  toggleDriverMode: () => void;
  updateDriverStatus: (isOnline: boolean) => Promise<void>;
  updateProfileImage: (imageUri: string) => Promise<void>;
  logout: () => Promise<void>;
  logoutAll: () => Promise<{ revoked_refresh_tokens: number }>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  driver: null,
  isDriverMode: false,
  token: null,
  refreshToken: null,
  tokenExpiresAt: null,
  isLoading: false,
  isInitialized: false,
  error: null,

  // ── Token helpers ──────────────────────────────────────────────────────── //

  setTokens: async (token: string, refreshToken: string, expiresIn: number, csrfToken?: string | null) => {
    const expiresAt = Date.now() + expiresIn * 1000;
    // Access token is memory-only (wiped on restart, stays within JWT TTL).
    // Only the refresh token is persisted to hardware-backed secure storage.
    setInMemoryToken(token);
    if (csrfToken !== undefined) setCsrfToken(csrfToken);
    await storage.setItem('refresh_token', refreshToken);
    await storage.setItem('token_expires_at', String(expiresAt));
    // Remove any previously-persisted access token from older app versions.
    await storage.deleteItem('auth_token');
    set({ token, refreshToken, tokenExpiresAt: expiresAt });
  },

  refreshTokens: async (): Promise<boolean> => {
    const storedRefresh = get().refreshToken ?? await storage.getItem('refresh_token');
    if (!storedRefresh) return false;
    try {
      const res = await api.post('/auth/refresh', { refresh_token: storedRefresh });
      const { token, refresh_token: newRefresh, expires_in, csrf_token } = res.data as RefreshTokenResponse;
      await get().setTokens(token, newRefresh, expires_in, csrf_token);
      return true;
    } catch (e: unknown) {
      // Only wipe the session when the server explicitly rejects the refresh
      // token (401). Network errors, timeouts, and 5xx are transient — keeping
      // the refresh token lets the next app launch / request try again instead
      // of forcing the user back to the OTP screen on a flaky connection.
      const status = isApiError(e) ? e.response?.status : undefined;
      if (status === 401) {
        console.log('[Auth] Refresh token rejected (401) — logging out');
        await get().logout();
      } else {
        console.log('[Auth] Token refresh failed transiently, keeping session:', status ?? (isApiError(e) ? e.message : String(e)));
      }
      return false;
    }
  },

  initialize: async () => {
    // Register the silent-refresh callback with the API client once (SEC-014)
    setRefreshCallback(() => get().refreshTokens());

    if (__DEV__) console.log("Auth initializing...");
    set({ isLoading: true });

    // Strategy:
    //   1. ALWAYS check for a stored backend JWT first.
    //   2. Only fall through to Firebase if there's no stored token.
    //   This prevents Firebase onAuthStateChanged (which fires with null
    //   when no Firebase phone-auth session exists) from deleting a
    //   perfectly valid backend JWT.

    const storedToken = await storage.getItem('auth_token');
    if (__DEV__) console.log('[Auth] Stored token:', storedToken ? 'EXISTS' : 'NULL');

    if (storedToken) {
      // ── Stored backend JWT path ──
      try {
        const response = await api.get('/auth/me', {
          headers: { Authorization: `Bearer ${storedToken}` }
        });
        const userData = response.data as User;

        if (__DEV__) console.log('[Auth] /auth/me →', {
          phone: userData?.phone,
          first_name: userData?.first_name,
          last_name: userData?.last_name,
          email: userData?.email,
          profile_complete: userData?.profile_complete,
          is_driver: userData?.is_driver,
          driver_onboarding_status: userData?.driver_onboarding_status,
          driver_onboarding_next_screen: userData?.driver_onboarding_next_screen,
        });

        await appCache.set(CACHE_KEYS.USER_PROFILE, userData, CACHE_CONFIG.USER_PROFILE_TTL);

        let driverData: Driver | null = null;
        // See refreshProfile for why we also gate on driver_onboarding_status:
        // it's the reliable "driver row exists" signal when is_driver/role
        // flags are stale on legacy user rows.
        const looksLikeDriver =
          !!userData.is_driver ||
          userData.role === 'driver' ||
          !!userData.driver_onboarding_status;
        if (looksLikeDriver) {
          try {
            const driverRes = await api.get('/drivers/me', {
              headers: { Authorization: `Bearer ${storedToken}` }
            });
            driverData = driverRes.data as Driver;
            await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, CACHE_CONFIG.USER_PROFILE_TTL);
          } catch (e: unknown) {
            if (isApiError(e) && e.response?.status === 404) {
              // No driver row — auto-create one from the user's profile.
              // The backend fills all required fields from the authenticated user.
              if (__DEV__) console.log('[Auth] No driver row on init — auto-registering');
              try {
                const regRes = await api.post('/drivers/register', {}, {
                  headers: { Authorization: `Bearer ${storedToken}` }
                });
                driverData = regRes.data as Driver;
                await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, CACHE_CONFIG.USER_PROFILE_TTL);
              } catch (regErr) {
                if (__DEV__) console.log('[Auth] Auto-register failed on init:', regErr);
              }
            } else {
              if (__DEV__) console.log('Failed to fetch driver data on init');
            }
          }
        }

        setInMemoryToken(storedToken);
        set({
          user: userData,
          driver: driverData,
          token: storedToken,
          isInitialized: true,
          isLoading: false
        });
        return; // Done — valid session restored
      } catch (error: unknown) {
        if (__DEV__) console.log('[Auth] Stored token invalid or expired:', isApiError(error) ? error.message : String(error));
        await storage.deleteItem('auth_token');
        // Fall through to no-session state below
      }
    }

    // ── No valid stored access token — try silent refresh ──
    // setTokens() keeps the access token in memory only but persists the
    // refresh token. On cold start (memory wiped), this is the normal path
    // to restore a session without forcing the user back to the OTP screen.
    const storedRefresh = await storage.getItem('refresh_token');
    if (storedRefresh) {
      const refreshed = await get().refreshTokens();
      if (refreshed) {
        const newToken = get().token;
        try {
          const meRes = await api.get('/auth/me');
          const userData = meRes.data as User;
          await appCache.set(CACHE_KEYS.USER_PROFILE, userData, CACHE_CONFIG.USER_PROFILE_TTL);

          let driverData: Driver | null = null;
          const looksLikeDriver =
            !!userData.is_driver ||
            userData.role === 'driver' ||
            !!userData.driver_onboarding_status;
          if (looksLikeDriver) {
            try {
              const driverRes = await api.get('/drivers/me');
              driverData = driverRes.data as Driver;
              await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, CACHE_CONFIG.USER_PROFILE_TTL);
            } catch (e: unknown) {
              if (isApiError(e) && e.response?.status === 404) {
                if (__DEV__) console.log('[Auth] No driver row on refresh-init — auto-registering');
                try {
                  const regRes = await api.post('/drivers/register', {});
                  driverData = regRes.data as Driver;
                  await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, CACHE_CONFIG.USER_PROFILE_TTL);
                } catch (regErr) {
                  if (__DEV__) console.log('[Auth] Auto-register failed on refresh-init:', regErr);
                }
              } else {
                if (__DEV__) console.log('Failed to fetch driver data on refresh-init');
              }
            }
          }

          set({
            user: userData,
            driver: driverData,
            token: newToken,
            isInitialized: true,
            isLoading: false,
          });
          return;
        } catch (e) {
          if (__DEV__) console.log('[Auth] Profile hydration after refresh failed:', e);
          // refreshTokens() already stored new tokens; if /auth/me fails
          // here, treat it as a failed session and fall through.
        }
      }
      // refreshTokens() on failure already called logout() which cleared state.
    }

    // ── No valid stored token ──
    // Check Firebase as a secondary auth source (only useful when firebase
    // phone-auth is actively configured and the user signed in via it).
    const firebaseAuthInstance = typeof auth.onAuthStateChanged === 'function' ? auth : null;
    if (firebaseAuthInstance) {
      // Safety timeout: if Firebase doesn't respond within 4s, force init
      setTimeout(() => {
        const state = get();
        if (!state.isInitialized) {
          if (__DEV__) console.log('[Auth] Firebase init timed out - forcing completion with no session');
          // Clear leftover tokens so the next launch isn't wedged on the
          // same stale state — this is the no-uninstall recovery path.
          clearAuthStorage();
          set({ user: null, driver: null, token: null, refreshToken: null, tokenExpiresAt: null, isInitialized: true, isLoading: false });
        }
      }, 4000);

      firebaseAuthInstance.onAuthStateChanged(async (firebaseUser: FirebaseUser | null) => {
        if (get().isInitialized) return; // Already resolved by timeout or previous call

        if (firebaseUser) {
          try {
            const token = await firebaseUser.getIdToken();
            if (__DEV__) console.log('[Auth] Got Firebase token');
            // Set in-memory token immediately so subsequent API calls don't hang on Firebase
            setInMemoryToken(token);

            let userData: User | null = null;
            let driverData: Driver | null = null;

            try {
              const response = await api.get('/auth/me');
              userData = response.data as User;
              if (userData) {
                await appCache.set(CACHE_KEYS.USER_PROFILE, userData, CACHE_CONFIG.USER_PROFILE_TTL);
              }
              const looksLikeDriver2 =
                !!userData?.is_driver ||
                userData?.role === 'driver' ||
                !!userData?.driver_onboarding_status;
              if (looksLikeDriver2) {
                try {
                  const driverRes = await api.get('/drivers/me');
                  driverData = driverRes.data as Driver;
                  await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, CACHE_CONFIG.USER_PROFILE_TTL);
                } catch (e) {
                  if (__DEV__) console.log('Failed to fetch driver data on init');
                }
              }
              set({ user: userData, driver: driverData, token, isInitialized: true, isLoading: false });
              await storage.setItem('auth_token', token);
            } catch (err) {
              if (__DEV__) console.log('[Auth] Firebase user but backend fetch failed');
              set({ isLoading: false, isInitialized: true, error: 'Failed to sync user' });
            }
          } catch (error: unknown) {
            if (__DEV__) console.log('[Auth] Failed to get Firebase token:', error);
            set({ isLoading: false, isInitialized: true, error: 'Failed to sync user' });
          }
        } else {
          // No Firebase user AND no stored token → truly logged out
          if (__DEV__) console.log('[Auth] No Firebase user, no stored token → logged out');
          // Belt-and-suspenders: even if earlier paths already cleared
          // these, make absolutely sure nothing lingers. Fixes the
          // "uninstall required" class of bugs where a transient (non-401)
          // refresh failure left refresh_token in SecureStore.
          await clearAuthStorage();
          await appCache.clearUserCache();
          set({ user: null, driver: null, token: null, refreshToken: null, tokenExpiresAt: null, isInitialized: true, isLoading: false });
        }
      });
    } else {
      // Firebase not available at all
      if (__DEV__) console.log('[Auth] No stored token, no Firebase → logged out');
      await clearAuthStorage();
      set({ user: null, driver: null, token: null, refreshToken: null, tokenExpiresAt: null, isInitialized: true, isLoading: false });
    }
  },

  verifyOTP: async (verificationId: string, code: string) => {
    try {
      set({ isLoading: true, error: null });

      const credential = PhoneAuthProvider.credential(verificationId, code);
      await signInWithCredential(auth, credential);

      // onAuthStateChanged will handle the rest
    } catch (error: unknown) {
      if (__DEV__) console.log('Verify OTP Error:', error);
      const message = (isApiError(error) && error.message) || 'Invalid verification code';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  createProfile: async (data: Parameters<AuthState['createProfile']>[0]) => {
    try {
      set({ isLoading: true, error: null });
      const response = await api.post<User>('/users/profile', data);
      set({ user: response.data, isLoading: false });
      // Re-fetch /auth/me so driver_onboarding_status gets computed
      // (the POST /users/profile response doesn't include it).
      try {
        const meRes = await api.get<User>('/auth/me');
        set({ user: meRes.data });
      } catch {}
    } catch (error: unknown) {
      const message = (isApiError(error) && error.response?.data?.detail) || 'Failed to create profile';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  fetchDriverProfile: async () => {
    try {
      const response = await api.get<Driver>('/drivers/me');
      set({ driver: response.data });
    } catch (error) {
      if (__DEV__) console.log('Failed to fetch driver profile');
      set({ driver: null });
    }
  },

  // Re-pulls /auth/me (which recomputes driver_onboarding_status on the
  // server) and /drivers/me so the UI reflects admin-side changes — e.g. a
  // driver flipping from pending_review to verified. Safe to call at any
  // time after init; no-op if there's no user/token yet.
  refreshProfile: async () => {
    if (!get().token) return;
    try {
      const meRes = await api.get('/auth/me');
      const userData = meRes.data as User;
      set({ user: userData });
      // A driver row exists iff the server returned a driver_onboarding_status
      // — that derivation only runs when there's a driver row (or role=driver).
      // This signal is more reliable than `is_driver` / `role`, which can be
      // stale on legacy user rows whose driver was created without flipping
      // those flags. Without this, /drivers/me is never called and the GO
      // button stays disabled because `driver` is null in the store.
      const looksLikeDriver =
        !!userData?.is_driver ||
        userData?.role === 'driver' ||
        !!userData?.driver_onboarding_status;
      if (looksLikeDriver) {
        try {
          const driverRes = await api.get('/drivers/me');
          set({ driver: driverRes.data as Driver });
        } catch (e: unknown) {
          if (__DEV__) console.log('refreshProfile: driver fetch failed', e);
          if (isApiError(e) && e.response?.status === 404) {
            // No driver row — auto-create one silently so the driver can
            // reach the home screen without going through become-driver.
            if (__DEV__) console.log('[Auth] No driver row on refresh — auto-registering');
            try {
              const regRes = await api.post('/drivers/register', {});
              set({ driver: regRes.data as Driver });
            } catch (regErr) {
              if (__DEV__) console.log('[Auth] Auto-register failed on refresh:', regErr);
              set({ driver: null });
            }
          }
        }
      }
    } catch (e) {
      if (__DEV__) console.log('refreshProfile: /auth/me failed', e);
    }
  },

  registerDriver: async (data: DriverRegistrationPayload) => {
    try {
      set({ isLoading: true, error: null });
      const response = await api.post<Driver>('/drivers/register', data);
      const user = get().user;
      const updatedUser = user ? { ...user, role: 'driver', is_driver: true } : user;
      set({
        driver: response.data,
        user: updatedUser,
        isLoading: false,
        isDriverMode: true
      });
    } catch (error: unknown) {
      const message = (isApiError(error) && error.response?.data?.detail) || 'Failed to register driver';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  toggleDriverMode: () => {
    const { isDriverMode, driver, fetchDriverProfile } = get();
    if (!isDriverMode && !driver) {
      fetchDriverProfile().then(() => {
        const { driver: newDriver } = get();
        if (newDriver) {
          set({ isDriverMode: true });
        }
      });
      return;
    }
    set({ isDriverMode: !isDriverMode });
  },

  updateDriverStatus: async (isOnline: boolean) => {
    const driver = get().driver;
    if (!driver?.id) {
      throw new Error('Driver ID not found');
    }
    try {
      await api.put(`/drivers/${driver.id}/status`, { is_online: isOnline });
      set({ driver: { ...driver, is_online: isOnline } });
    } catch (error: unknown) {
      if (__DEV__) console.log('Failed to update status');
      throw error;
    }
  },

  logout: async () => {
    // Uber/Lyft-style: a driver explicitly signing out must flip offline
    // before the socket drops and tokens get wiped. Without this, the
    // admin live-monitoring map (and any rider mid-match) would keep
    // seeing the driver as online until their presence TTL expired. We
    // swallow errors — a flaky network shouldn't block the user from
    // signing out — and fall through to the normal cleanup.
    const { driver, token } = get();
    if (driver?.id && token) {
      try {
        await api.put(`/drivers/${driver.id}/status`, { is_online: false });
      } catch (error) {
        if (__DEV__) console.log('[Auth] go-offline on logout failed (non-fatal):', error);
      }
    }

    try {
      if (typeof auth.onAuthStateChanged === 'function') {
        await signOut(auth);
      }
    } catch (error) {
      if (__DEV__) console.log('Logout error:', error);
    }
    setInMemoryToken(null);
    setCsrfToken(null);
    await storage.deleteItem('auth_token');
    await storage.deleteItem('refresh_token');
    await storage.deleteItem('token_expires_at');
    // Clear user cache on logout
    await appCache.clearUserCache();
    set({ user: null, driver: null, token: null, refreshToken: null, tokenExpiresAt: null, isDriverMode: false });
  },

  // "Sign out of all devices" — closes B-P1-13. Backend bumps
  // users.token_version (kills every in-flight access token on its
  // next request via dependencies.py middleware re-read) and revokes
  // every refresh token row for the user. Pairs with the B-P1-3 reuse-
  // detection cascade: this is the user-driven recovery path the
  // runbook (docs/runbooks/auth-tokens.md) sends compromised users to.
  // Falls through to logout() either way so the local session ends
  // even if the network call failed (we don't want to leave the user
  // sitting on a screen that thinks they're signed in).
  logoutAll: async () => {
    let revoked = 0;
    try {
      const res = await api.post<{ success: boolean; revoked_refresh_tokens: number }>('/auth/logout-all');
      revoked = Number(res.data?.revoked_refresh_tokens ?? 0);
    } catch (error: unknown) {
      if (__DEV__) console.log('logout-all backend call failed:', isApiError(error) ? (error.message ?? error) : String(error));
    } finally {
      await get().logout();
    }
    return { revoked_refresh_tokens: revoked };
  },

  updateProfileImage: async (imageUri: string) => {
    try {
      set({ isLoading: true, error: null });
      const formData = new FormData();
      const filename = imageUri.split('/').pop() || 'profile.jpg';
      const match = /\.([\w]+)$/.exec(filename);
      const type = match ? `image/${match[1] === 'jpg' ? 'jpeg' : match[1]}` : 'image/jpeg';

      formData.append('file', {
        uri: imageUri,
        name: filename,
        type,
      } as unknown as File);

      // The api client detects FormData and lets fetch set the multipart
      // boundary itself — do not pass a Content-Type header here.
      const response = await api.put<User>('/users/profile-image', formData);
      set({ user: response.data, isLoading: false });

      // Invalidate user cache to reflect the new profile image
      await appCache.remove(CACHE_KEYS.USER_PROFILE);
    } catch (error: unknown) {
      const message = (isApiError(error) && error.response?.data?.detail) || 'Failed to upload profile image';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  clearError: () => set({ error: null }),
}));
