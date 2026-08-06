import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import api, { setCsrfToken, setInMemoryToken, setRefreshCallback, setSuppressRefreshSignOut } from '../api/client';
import { appCache, CACHE_KEYS } from '../cache';
import { SESSION_ENDED_KEY } from '../auth/sessionMarker';

// Last-known profile is cached with a long TTL so the driver/rider still sees
// their photo, name, phone, and vehicle after a long idle period or when the
// device is offline on relaunch — instead of the screen blanking to "N/A" /
// "Add Vehicle" while the network refresh is in flight. Wiped on logout via
// appCache.clearUserCache(), so it does not extend PII retention beyond the
// active session (TanStack Query already persists /drivers/me for 24h; this
// brings the user row to parity).
const PROFILE_CACHE_TTL = 7 * 24 * 60 * 60 * 1000; // 7 days

// Registry of callbacks that must run when the user logs out. Other stores
// (rideStore, driverStore) register here on mount to wipe per-session state
// so a subsequent login never sees ghost data from the previous user.
// Using callbacks instead of direct imports avoids circular dependencies
// between the shared package and app-specific stores.
type LogoutCallback = () => void | Promise<void>;
const _logoutCallbacks: Set<LogoutCallback> = new Set();

export function registerLogoutCallback(cb: LogoutCallback): () => void {
  _logoutCallbacks.add(cb);
  // Return an unregister function so stores can clean up on unmount.
  return () => _logoutCallbacks.delete(cb);
}

async function _runLogoutCallbacks(): Promise<void> {
  for (const cb of _logoutCallbacks) {
    try {
      await cb();
    } catch (e) {
      if (__DEV__) console.log('[Auth] logoutCallback failed (non-fatal):', e);
    }
  }
}

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

// Every storage key holding session material. ONE list, because the two places
// that wipe session state (clearAuthStorage and logout) had drifted apart and
// both were missing the driver app's background-task keys:
//
//   • clearAuthStorage cleared 3 of 6 — it left BOTH access tokens behind.
//   • logout cleared 4 of 6 — it left the background task's cached copy behind.
//
// The consequence was a live access token surviving sign-out. The driver app's
// headless location task reads `bg_access_token` directly
// (driver-app/utils/backgroundLocation.ts::getBackgroundAuthToken) and only
// stopBackgroundLocation() ever deleted it — which runs solely from the
// go-offline branch of toggleOnline. A driver who signed out WITHOUT first going
// offline therefore kept uploading location for up to the token's remaining TTL,
// on a session they had ended. Adding a key here now covers both paths.
const AUTH_STORAGE_KEYS = [
  'auth_token',              // legacy; setTokens deletes it, cleared here for old installs
  'fg_access_token',         // foreground access token, read by the driver bg task
  'bg_access_token',         // driver bg task's own cache of the above
  'bg_access_token_expires', // …and its expiry
  'refresh_token',
  'token_expires_at',
] as const;

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
      for (const key of AUTH_STORAGE_KEYS) sessionStorage.removeItem(key);
      return;
    }
    for (const key of AUTH_STORAGE_KEYS) await SecureStore.deleteItemAsync(key);
    // Record the end of the session so the headless contexts can see it. They
    // have no access to this store, and cannot infer it from missing tokens —
    // see shared/auth/sessionMarker.ts.
    await SecureStore.setItemAsync(SESSION_ENDED_KEY, '1');
  } catch (e) {
    // Best-effort — never let a storage error block the login screen.
    if (__DEV__) console.log('[Auth] clearAuthStorage failed:', e);
  }
}

// Platform-safe secure storage
// Web relies entirely on HttpOnly cookies set by the backend — no client-side
// token storage, so XSS cannot exfiltrate session tokens. Native uses SecureStore.
const storage = {
  async getItem(key: string): Promise<string | null> {
    try {
      if (Platform.OS === 'web') return null;
      return await SecureStore.getItemAsync(key);
    } catch (e) {
      if (__DEV__) {
        console.warn('[Storage] SecureStore.getItemAsync failed — tokens will not persist across restarts:',
          e instanceof Error ? e.message : e);
      }
      return null;
    }
  },
  async setItem(key: string, value: string): Promise<void> {
    try {
      if (Platform.OS === 'web') return;
      return await SecureStore.setItemAsync(key, value);
    } catch (e) {
      if (__DEV__) {
        console.warn('[Storage] SecureStore.setItemAsync failed — tokens will not persist across restarts:',
          e instanceof Error ? e.message : e);
      }
    }
  },
  async deleteItem(key: string): Promise<void> {
    try {
      if (Platform.OS === 'web') return;
      return await SecureStore.deleteItemAsync(key);
    } catch (e) {
      if (__DEV__) {
        console.warn('[Storage] SecureStore.deleteItemAsync failed — tokens will not persist across restarts:',
          e instanceof Error ? e.message : e);
      }
    }
  },
};

export interface Driver {
  id: string;
  user_id: string;
  driver_code?: string;
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
  is_rider?: boolean;
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
  // Access-token lifetime in seconds. OPTIONAL on purpose: the backend's
  // RefreshResponse did not carry this field until 2026-07-29, and an installed
  // app can still be talking to a backend that predates that fix. Never assume
  // it is present — derive via ttlSecondsFromRefreshResponse below.
  expires_in?: number;
  // ISO-8601 absolute expiry. Always sent; the fallback source for the TTL.
  access_expires_at?: string;
  csrf_token?: string | null;
}

// Access-token lifetime, in seconds, used when the server's reported value is
// missing or unusable. Mirrors the backend's ACCESS_TOKEN_EXPIRE_MINUTES (15)
// and the `expires_in ?? 900` default the OTP screens already apply.
const DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 900;

function _isUsableTtl(seconds: unknown): seconds is number {
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0;
}

/**
 * Coerce a reported access-token lifetime into a usable positive seconds value.
 *
 * A non-finite value here cost drivers a release's worth of sign-outs:
 * /auth/refresh omitted `expires_in`, so `Date.now() + expiresIn * 1000`
 * evaluated to NaN. `tokenExpiresAt` then read as falsy, and
 * ensureFreshToken()'s `!tokenExpiresAt` guard silently disabled the proactive
 * 2-minute refresh for the rest of the session — every expiry became a reactive
 * 401 burst. The driver app also persists this as SecureStore
 * `token_expires_at`, which its headless location task parses to authorise
 * batch uploads, so the string "NaN" disabled those too.
 *
 * Defaulting is safe in both directions: guessing too long costs one reactive
 * 401 that the interceptor already recovers from; guessing too short costs one
 * extra token rotation.
 */
function usableTtlSeconds(seconds: unknown): number {
  return _isUsableTtl(seconds) ? seconds : DEFAULT_ACCESS_TOKEN_TTL_SECONDS;
}

/**
 * Seconds until the access token expires, from whichever field the server sent.
 *
 * `expires_in` is canonical. When it is absent, derive from the absolute
 * `access_expires_at` timestamp rather than discarding real information and
 * falling back to a guess.
 */
function ttlSecondsFromRefreshResponse(data: RefreshTokenResponse | undefined): number {
  if (_isUsableTtl(data?.expires_in)) return data!.expires_in!;

  const absolute = data?.access_expires_at;
  if (absolute) {
    const parsedMs = Date.parse(absolute);
    if (Number.isFinite(parsedMs)) {
      const seconds = Math.floor((parsedMs - Date.now()) / 1000);
      // A non-positive result means the server's expiry is already in the past
      // (clock skew, or a stale response) — not usable, fall through.
      if (seconds > 0) return seconds;
    }
  }
  return DEFAULT_ACCESS_TOKEN_TTL_SECONDS;
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
  sessionRecoverable: boolean;     // transient refresh failure with valid refresh token in storage
  error: string | null;

  // Actions
  initialize: () => Promise<void>;
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
  updateDriverStatus: (isOnline: boolean, location?: { lat: number; lng: number }) => Promise<void>;
  updateProfileImage: (imageUri: string) => Promise<void>;
  /**
   * End the local session. Also revokes it server-side (POST /auth/logout)
   * unless `revokeServerSession: false` — pass that only where the credential
   * is already dead or already revoked, since the call needs a live token.
   */
  logout: (options?: { revokeServerSession?: boolean }) => Promise<void>;
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
  sessionRecoverable: false,
  error: null,

  // ── Token helpers ──────────────────────────────────────────────────────── //

  setTokens: async (token: string, refreshToken: string, expiresIn: number, csrfToken?: string | null) => {
    // Guard the arithmetic at the ONE choke point that both sets tokenExpiresAt
    // and persists token_expires_at, so no caller — present or future — can put
    // a non-finite value into state or secure storage. See usableTtlSeconds for
    // why this is worth a guard rather than trusting the caller.
    const ttlSeconds = usableTtlSeconds(expiresIn);
    if (ttlSeconds !== expiresIn) {
      // Deliberately NOT __DEV__-gated: this means the server told us something
      // unusable about a live session, and production silence is how the
      // original bug survived a release. No token material in the message.
      console.warn(
        `[Auth] Unusable access-token TTL (${String(expiresIn)}) — defaulting to ${ttlSeconds}s. ` +
          'Proactive refresh would have been disabled for this session.',
      );
    }
    const expiresAt = Date.now() + ttlSeconds * 1000;
    setInMemoryToken(token);
    if (csrfToken !== undefined) setCsrfToken(csrfToken);
    // A live session exists again — clear the end-of-session marker before
    // writing tokens, so a headless task that fires mid-write can never see
    // fresh tokens alongside a stale "signed out" marker and tear itself down.
    await storage.deleteItem(SESSION_ENDED_KEY);
    await storage.setItem('refresh_token', refreshToken);
    await storage.setItem('token_expires_at', String(expiresAt));
    // Persist the access token so the background location task (which runs
    // in a separate JS context with no shared memory) can read it directly
    // instead of independently rotating the shared refresh token.
    await storage.setItem('fg_access_token', token);
    // Remove any previously-persisted access token from older app versions.
    await storage.deleteItem('auth_token');
    set({ token, refreshToken, tokenExpiresAt: expiresAt });
  },

  refreshTokens: async (): Promise<boolean> => {
    // Prefer the freshest persisted refresh token over the in-memory copy.
    // On the driver app a headless background location task rotates the shared
    // SecureStore `refresh_token` independently of this foreground context;
    // reading storage first lets the foreground pick up a rotation the
    // background already performed instead of replaying a stale in-memory
    // token (which the backend then 401s as a benign rotation race).
    let candidate = (await storage.getItem('refresh_token')) ?? get().refreshToken ?? null;
    if (!candidate) {
      // No refresh token but an active session: the session cannot be
      // recovered, so tear it down here — the interceptor's G2 backstop no
      // longer fires once a refresh was attempted, and without this the user
      // would sit in auth limbo (API calls 401ing behind a live-looking UI).
      // NOT awaited: logout()'s go-offline PUT can itself 401 and queue
      // behind the interceptor's in-flight _refreshPromise — which is this
      // very call — so awaiting would deadlock every queued request.
      // Cold-start initialize() is unaffected (it only refreshes when a
      // stored refresh token exists).
      if (get().token || get().user) {
        // Credential is already unusable (no refresh token) — skip the server
        // revoke so it can't 401 into the interceptor's in-flight refresh.
        void get().logout({ revokeServerSession: false });
      }
      return false;
    }

    // Own the sign-out decision: a 401 from /auth/refresh during this attempt
    // must not auto-sign-out at the interceptor (see setSuppressRefreshSignOut)
    // until we've checked for a fresher rotated-forward token below.
    setSuppressRefreshSignOut(true);
    try {
      // Up to 2 attempts: the first uses the freshest token we have; on a 401
      // we re-read storage and, if another context rotated the token forward in
      // the meantime, retry once with that value. A short wait covers the
      // simultaneous-refresh window where the winner hasn't persisted yet.
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const res = await api.post('/auth/refresh', { refresh_token: candidate });
          const data = res.data as RefreshTokenResponse;
          const { token, refresh_token: newRefresh, csrf_token } = data;
          await get().setTokens(token, newRefresh, ttlSecondsFromRefreshResponse(data), csrf_token);
          return true;
        } catch (e: unknown) {
          // The /auth/refresh path rejects with a raw fetch Response (HTTP
          // status on `.status`); all other client errors throw SpinrApiError
          // (status on both `.status` and `.response.status`). Read both shapes
          // so a 401 is detected no matter which path rejected — otherwise the
          // retry below never fires and a dead session never logs out.
          const err = e as { status?: number; response?: { status?: number } };
          const status = (typeof err?.status === 'number' ? err.status : undefined) ?? err?.response?.status;
          if (status !== 401) {
            // Network errors, timeouts, and 5xx are transient — keep the refresh
            // token so the next app launch / request can try again instead of
            // forcing the user back to the OTP screen on a flaky connection.
            console.log('[Auth] Token refresh failed transiently, keeping session:', status ?? (isApiError(e) ? e.message : String(e)));
            return false;
          }
          // 401: the token we sent was rejected. Before tearing down the
          // session, check whether another context (the background task) has
          // rotated the shared refresh token forward in secure storage. If so,
          // this is the foreground/background rotation race — retry once with
          // the fresher value rather than logging the user out.
          if (attempt === 0) {
            // The winner of the race may not have persisted its rotated token
            // yet; poll storage a few times (immediate, then short backoffs)
            // so we still recover on slower hardware instead of a single fixed
            // wait. Retry the moment a fresher token appears.
            // Explicit annotation: `candidate` is reassigned from `latest`
            // below, and `latest` is initialised from `candidate` — without a
            // type here TS can't resolve the mutual reference and falls back to
            // implicit-any on `latest` (TS7022). `string` matches what TS
            // originally inferred (candidate is narrowed to non-null by the
            // guard above), so this doesn't perturb `candidate`'s type downstream.
            let latest: string = candidate;
            for (const waitMs of [0, 250, 500, 1000, 2000]) {
              if (waitMs) await new Promise((r) => setTimeout(r, waitMs));
              const v = await storage.getItem('refresh_token');
              if (v && v !== candidate) { latest = v; break; }
            }
            if (latest !== candidate) {
              candidate = latest;
              continue;
            }
          }
          // Genuinely rejected (token unchanged, or the retry also failed):
          // the session is dead — revoked, expired, or post-cascade. Clear it.
          console.log('[Auth] Refresh token rejected (401) — logging out');
          // Backend already rejected this credential; nothing left to revoke.
          await get().logout({ revokeServerSession: false });
          return false;
        }
      }
      return false;
    } finally {
      setSuppressRefreshSignOut(false);
    }
  },

  initialize: async () => {
    // Register the silent-refresh callback with the API client once (SEC-014)
    setRefreshCallback(() => get().refreshTokens());

    if (__DEV__) console.log("Auth initializing...");
    set({ isLoading: true });

    // Strategy: access tokens are memory-only (never persisted).
    // On cold start, go straight to the refresh token path.
    // The refresh token flow re-hydrates the session without forcing
    // the user back to the OTP screen.

    // ── No stored access token — try silent refresh ──
    // setTokens() keeps the access token in memory only but persists the
    // refresh token. On cold start (memory wiped), this is the normal path
    // to restore a session without forcing the user back to the OTP screen.
    const storedRefresh = await storage.getItem('refresh_token');
    if (storedRefresh) {
      // Optimistic hydration: paint the last-known profile from cache before
      // the network round-trips below resolve, so the first frame after the
      // splash never flashes blank ("N/A" / "Add Vehicle"). Fresh data from
      // /auth/me + /drivers/me overwrites this on success.
      try {
        const [cachedUser, cachedDriver] = await Promise.all([
          appCache.get<User>(CACHE_KEYS.USER_PROFILE),
          appCache.get<Driver>(CACHE_KEYS.DRIVER_PROFILE),
        ]);
        if (cachedUser) set({ user: cachedUser });
        if (cachedDriver) set({ driver: cachedDriver });
      } catch {
        // Cache read is best-effort — never block init on it.
      }

      const refreshed = await get().refreshTokens();
      if (refreshed) {
        const newToken = get().token;
        try {
          const meRes = await api.get('/auth/me');
          const userData = meRes.data as User;
          await appCache.set(CACHE_KEYS.USER_PROFILE, userData, PROFILE_CACHE_TTL);

          let driverData: Driver | null = null;
          const looksLikeDriver =
            !!userData.is_driver ||
            !!userData.driver_onboarding_status;
          if (looksLikeDriver) {
            try {
              const driverRes = await api.get('/drivers/me');
              driverData = driverRes.data as Driver;
              await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, PROFILE_CACHE_TTL);
            } catch (e: unknown) {
              if (isApiError(e) && e.response?.status === 404) {
                if (__DEV__) console.log('[Auth] No driver row on refresh-init — auto-registering');
                try {
                  const regRes = await api.post('/drivers/register', {});
                  driverData = regRes.data as Driver;
                  await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, PROFILE_CACHE_TTL);
                } catch (regErr) {
                  if (__DEV__) console.log('[Auth] Auto-register failed on refresh-init:', regErr);
                }
              } else {
                // Network/transient failure fetching the driver row — keep the
                // cached driver (set optimistically above) so vehicle details
                // stay visible instead of reverting to "Add Vehicle".
                if (__DEV__) console.log('Failed to fetch driver data on refresh-init');
                driverData = get().driver;
              }
            }
          }

          set({
            user: userData,
            driver: driverData,
            token: newToken,
            isInitialized: true,
            isLoading: false,
            sessionRecoverable: false,
          });
          return;
        } catch (e) {
          if (__DEV__) console.log('[Auth] Profile hydration after refresh failed:', e);
          // Refresh succeeded (access token is valid) but /auth/me failed —
          // almost always a transient network blip right after resume, or the
          // device is offline. Rather than bouncing the driver to the OTP
          // screen, fall back to the last-known cached profile and keep the
          // session so their photo/name/phone/vehicle stay on screen.
          const cachedUser = await appCache.get<User>(CACHE_KEYS.USER_PROFILE);
          if (cachedUser && newToken) {
            const cachedDriver = await appCache.get<Driver>(CACHE_KEYS.DRIVER_PROFILE);
            set({
              user: cachedUser,
              driver: cachedDriver,
              token: newToken,
              isInitialized: true,
              isLoading: false,
            });
            return;
          }
          // No cached profile to fall back on — clear the in-memory token so
          // the app doesn't send a stale Authorization header while Zustand
          // thinks the user is logged out.
          setInMemoryToken(null);
          setCsrfToken(null);
        }
      }

      // refreshTokens() returned false OR refresh succeeded but /auth/me
      // failed. Two sub-cases:
      // A. 401 → refresh token is expired/revoked → logout() already ran,
      //    refresh_token already deleted from SecureStore. Fall through.
      // B. Transient error (5xx, timeout, "Network request failed") →
      //    refresh token is still valid and still in SecureStore. Do NOT
      //    delete it — the next app launch should retry.
      if (get().refreshToken || await storage.getItem('refresh_token')) {
        if (__DEV__) console.log('[Auth] Refresh failed transiently — session recoverable on resume');
        setInMemoryToken(null);
        setCsrfToken(null);
        set({ user: null, driver: null, token: null, refreshToken: null, tokenExpiresAt: null, isInitialized: true, isLoading: false, sessionRecoverable: true });
        return;
      }
    }

    // ── No valid stored token → logged out ──
    if (__DEV__) console.log('[Auth] No stored token → logged out');
    await clearAuthStorage();
    set({ user: null, driver: null, token: null, refreshToken: null, tokenExpiresAt: null, isInitialized: true, isLoading: false });
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
      // Keep the offline-fallback cache warm so a later cold start / resume
      // still has a recent profile to paint while the network refresh runs.
      appCache.set(CACHE_KEYS.USER_PROFILE, userData, PROFILE_CACHE_TTL).catch(() => {});
      // A driver row exists iff the server returned a driver_onboarding_status
      // — that derivation only runs when there's a driver row (or role=driver).
      // This signal is more reliable than `is_driver` / `role`, which can be
      // stale on legacy user rows whose driver was created without flipping
      // those flags. Without this, /drivers/me is never called and the GO
      // button stays disabled because `driver` is null in the store.
      const looksLikeDriver =
        !!userData?.is_driver ||
        !!userData?.driver_onboarding_status;
      if (looksLikeDriver) {
        try {
          const driverRes = await api.get('/drivers/me');
          set({ driver: driverRes.data as Driver });
          appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverRes.data as Driver, PROFILE_CACHE_TTL).catch(() => {});
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
      const updatedUser = user ? { ...user, role: 'driver', is_driver: true, is_rider: user.is_rider ?? true } : user;
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

  updateDriverStatus: async (isOnline: boolean, location?: { lat: number; lng: number }) => {
    const driver = get().driver;
    if (!driver?.id) {
      throw new Error('Driver ID not found');
    }
    try {
      // Send current GPS alongside the online flip when available. Without
      // this, the driver row sits at the (0, 0) registration default until
      // the first background location-batch arrives — during that window
      // the rider /drivers/nearby and admin monitoring views can't place
      // the driver on the map.
      const body: { is_online: boolean; lat?: number; lng?: number } = { is_online: isOnline };
      if (isOnline && location && Number.isFinite(location.lat) && Number.isFinite(location.lng)) {
        body.lat = location.lat;
        body.lng = location.lng;
      }
      await api.put(`/drivers/${driver.id}/status`, body);
      set({ driver: { ...driver, is_online: isOnline } });
    } catch (error: unknown) {
      if (__DEV__) console.log('Failed to update status');
      throw error;
    }
  },

  logout: async (options) => {
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

    // End the session server-side. Without this the refresh token stayed valid
    // for its full 30 days after a sign-out, so the session could be resurrected
    // from a stolen token, and the access token was trusted for its remaining
    // ~15 minutes — which is what let a signed-out driver app keep uploading GPS.
    // The backend also tombstones the session so opt-in ingest paths can reject
    // that still-valid access token.
    //
    // Ordered after the go-offline PUT and before the token wipe, because it
    // needs the live credential to authenticate.
    //
    // Skipped via revokeServerSession:false on the paths where the credential is
    // already known-dead (refresh rejected, interceptor backstop) or already
    // revoked (logoutAll). Calling it there would 401 and queue behind the
    // interceptor's in-flight refresh — the same deadlock the go-offline PUT
    // comment above warns about — for no benefit.
    if (options?.revokeServerSession !== false && token) {
      try {
        await api.post('/auth/logout');
      } catch (error) {
        // Best-effort: the local session still ends. A failure here leaves the
        // refresh token live until its own expiry, which is the pre-existing
        // behaviour, so it must not block the user from signing out.
        if (__DEV__) console.log('[Auth] server logout failed (non-fatal):', error);
      }
    }


    setInMemoryToken(null);
    setCsrfToken(null);
    // Drive this from AUTH_STORAGE_KEYS, not a hand-written list — the previous
    // hand-written one omitted the driver background task's cached access token,
    // which then outlived the session it belonged to.
    for (const key of AUTH_STORAGE_KEYS) await storage.deleteItem(key);
    // Positive evidence that this session ended, for the headless contexts that
    // cannot read this store (see shared/auth/sessionMarker.ts). Written before
    // the logout callbacks below so the driver-app teardown — and any headless
    // task that fires while it runs — both observe it.
    await storage.setItem(SESSION_ENDED_KEY, '1');
    // Clear user cache on logout
    await appCache.clearUserCache();
    set({ user: null, driver: null, token: null, refreshToken: null, tokenExpiresAt: null, isDriverMode: false, sessionRecoverable: false });
    // Reset all registered per-session stores (rideStore, driverStore) so a
    // subsequent login never sees ghost data from the previous user.
    await _runLogoutCallbacks();
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
      // /auth/logout-all already bumped token_version and revoked every refresh
      // token row, so a second per-session revoke would be redundant.
      await get().logout({ revokeServerSession: false });
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
