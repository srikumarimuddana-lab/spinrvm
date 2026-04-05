import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { auth } from '../config/firebaseConfig';
import { PhoneAuthProvider, signInWithCredential, signOut, User as FirebaseUser } from 'firebase/auth';
import api, { setInMemoryToken } from '../api/client';
import { appCache, CACHE_KEYS, CACHE_CONFIG } from '../cache';

// Platform-safe secure storage
const storage = {
  async getItem(key: string): Promise<string | null> {
    try {
      if (Platform.OS === 'web') {
        return localStorage.getItem(key);
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
        localStorage.setItem(key, value);
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
        localStorage.removeItem(key);
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
  [key: string]: any;
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
  // Driver onboarding state machine (computed server-side on every /auth/me).
  // Null for riders. Clients should route on this rather than profile_complete.
  driver_onboarding_status?: DriverOnboardingStatus | null;
  driver_onboarding_detail?: string | null;
  driver_onboarding_next_screen?: string | null;
}

interface AuthState {
  user: User | null;
  driver: Driver | null;
  isDriverMode: boolean;
  token: string | null;
  isLoading: boolean;
  isInitialized: boolean;
  error: string | null;

  // Actions
  initialize: () => Promise<void>;
  verifyOTP: (verificationId: string, code: string) => Promise<void>;
  createProfile: (data: { first_name: string; last_name: string; email: string; gender: string }) => Promise<void>;
  fetchDriverProfile: () => Promise<void>;
  refreshProfile: () => Promise<void>;
  registerDriver: (data: any) => Promise<void>;
  toggleDriverMode: () => void;
  updateDriverStatus: (isOnline: boolean) => Promise<void>;
  updateProfileImage: (imageUri: string) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set: any, get: any) => ({
  user: null,
  driver: null,
  isDriverMode: false,
  token: null,
  isLoading: false,
  isInitialized: false,
  error: null,

  initialize: async () => {
    console.log('Auth initializing...');
    set({ isLoading: true });

    // Strategy:
    //   1. ALWAYS check for a stored backend JWT first.
    //   2. Only fall through to Firebase if there's no stored token.
    //   This prevents Firebase onAuthStateChanged (which fires with null
    //   when no Firebase phone-auth session exists) from deleting a
    //   perfectly valid backend JWT.

    const storedToken = await storage.getItem('auth_token');
    console.log('[Auth] Stored token:', storedToken ? 'EXISTS' : 'NULL');

    if (storedToken) {
      // ── Stored backend JWT path ──
      try {
        const response = await api.get('/auth/me', {
          headers: { Authorization: `Bearer ${storedToken}` }
        });
        const userData = response.data as User;

        console.log('[Auth] /auth/me →', {
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
          !!(userData as any).is_driver ||
          (userData as any).role === 'driver' ||
          !!(userData as any).driver_onboarding_status;
        if (looksLikeDriver) {
          try {
            const driverRes = await api.get('/drivers/me', {
              headers: { Authorization: `Bearer ${storedToken}` }
            });
            driverData = driverRes.data as Driver;
            await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, CACHE_CONFIG.USER_PROFILE_TTL);
          } catch (e) {
            console.log('Failed to fetch driver data on init');
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
      } catch (error: any) {
        console.log('[Auth] Stored token invalid or expired:', error.message);
        await storage.deleteItem('auth_token');
        // Fall through to no-session state below
      }
    }

    // ── No valid stored token ──
    // Check Firebase as a secondary auth source (only useful when firebase
    // phone-auth is actively configured and the user signed in via it).
    if (typeof auth.onAuthStateChanged === 'function') {
      // Safety timeout: if Firebase doesn't respond within 4s, force init
      setTimeout(() => {
        const state = get();
        if (!state.isInitialized) {
          console.log('[Auth] Firebase init timed out - forcing completion with no session');
          set({ user: null, driver: null, token: null, isInitialized: true, isLoading: false });
        }
      }, 4000);

      auth.onAuthStateChanged(async (firebaseUser: any) => {
        if (get().isInitialized) return; // Already resolved by timeout or previous call

        if (firebaseUser) {
          try {
            const token = await firebaseUser.getIdToken();
            console.log('[Auth] Got Firebase token');

            let userData: User | null = null;
            let driverData: Driver | null = null;

            try {
              const response = await api.get('/auth/me');
              userData = response.data as User;
              if (userData) {
                await appCache.set(CACHE_KEYS.USER_PROFILE, userData, CACHE_CONFIG.USER_PROFILE_TTL);
              }
              const looksLikeDriver2 =
                !!(userData as any)?.is_driver ||
                (userData as any)?.role === 'driver' ||
                !!(userData as any)?.driver_onboarding_status;
              if (looksLikeDriver2) {
                try {
                  const driverRes = await api.get('/drivers/me');
                  driverData = driverRes.data as Driver;
                  await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, CACHE_CONFIG.USER_PROFILE_TTL);
                } catch (e) {
                  console.log('Failed to fetch driver data on init');
                }
              }
              set({ user: userData, driver: driverData, token, isInitialized: true, isLoading: false });
              await storage.setItem('auth_token', token);
            } catch (err) {
              console.log('[Auth] Firebase user but backend fetch failed');
              set({ isLoading: false, isInitialized: true, error: 'Failed to sync user' });
            }
          } catch (error: any) {
            console.log('[Auth] Failed to get Firebase token:', error);
            set({ isLoading: false, isInitialized: true, error: 'Failed to sync user' });
          }
        } else {
          // No Firebase user AND no stored token → truly logged out
          console.log('[Auth] No Firebase user, no stored token → logged out');
          await appCache.clearUserCache();
          set({ user: null, driver: null, token: null, isInitialized: true, isLoading: false });
        }
      });
    } else {
      // Firebase not available at all
      console.log('[Auth] No stored token, no Firebase → logged out');
      set({ user: null, driver: null, token: null, isInitialized: true, isLoading: false });
    }
  },

  verifyOTP: async (verificationId: string, code: string) => {
    try {
      set({ isLoading: true, error: null });

      const credential = PhoneAuthProvider.credential(verificationId, code);
      await signInWithCredential(auth, credential);

      // onAuthStateChanged will handle the rest
    } catch (error: any) {
      console.log('Verify OTP Error:', error);
      const message = error.message || 'Invalid verification code';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  createProfile: async (data: any) => {
    try {
      set({ isLoading: true, error: null });
      const response = await api.post('/users/profile', data);
      set({ user: response.data, isLoading: false });
    } catch (error: any) {
      const message = error.response?.data?.detail || 'Failed to create profile';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  fetchDriverProfile: async () => {
    try {
      const response = await api.get('/drivers/me');
      set({ driver: response.data });
    } catch (error) {
      console.log('Failed to fetch driver profile');
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
        !!(userData as any)?.is_driver ||
        (userData as any)?.role === 'driver' ||
        !!(userData as any)?.driver_onboarding_status;
      if (looksLikeDriver) {
        try {
          const driverRes = await api.get('/drivers/me');
          set({ driver: driverRes.data as Driver });
        } catch (e) {
          console.log('refreshProfile: driver fetch failed', e);
        }
      }
    } catch (e) {
      console.log('refreshProfile: /auth/me failed', e);
    }
  },

  registerDriver: async (data: any) => {
    try {
      set({ isLoading: true, error: null });
      const response = await api.post('/drivers/register', data);
      const user = get().user;
      const updatedUser = user ? { ...user, role: 'driver', is_driver: true } : user;
      set({
        driver: response.data,
        user: updatedUser,
        isLoading: false,
        isDriverMode: true
      });
    } catch (error: any) {
      const message = error.response?.data?.detail || 'Failed to register driver';
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
    } catch (error: any) {
      console.log('Failed to update status');
      throw error;
    }
  },

  logout: async () => {
    try {
      if (typeof auth.onAuthStateChanged === 'function') {
        await signOut(auth);
      }
    } catch (error) {
      console.log('Logout error:', error);
    }
    setInMemoryToken(null);
    await storage.deleteItem('auth_token');
    // Clear user cache on logout
    await appCache.clearUserCache();
    set({ user: null, driver: null, token: null, isDriverMode: false });
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
      } as any);

      // The api client detects FormData and lets fetch set the multipart
      // boundary itself — do not pass a Content-Type header here.
      const response = await api.put('/users/profile-image', formData);
      set({ user: response.data, isLoading: false });

      // Invalidate user cache to reflect the new profile image
      await appCache.remove(CACHE_KEYS.USER_PROFILE);
    } catch (error: any) {
      const message = error.response?.data?.detail || 'Failed to upload profile image';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  clearError: () => set({ error: null }),
}));
