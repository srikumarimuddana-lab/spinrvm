import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { auth } from '../config/firebaseConfig';
import { PhoneAuthProvider, signInWithCredential, signOut, User as FirebaseUser } from 'firebase/auth';
import api from '../api/client';
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

    // Safety timeout: if Firebase doesn't respond within 4s, force init to prevent splash screen hang
    setTimeout(() => {
      const state = get();
      if (!state.isInitialized) {
        console.log('Auth init timed out - forcing completion');
        set({ isInitialized: true, isLoading: false });
      }
    }, 4000);

    // Check if Firebase Auth is actually available
    if (typeof auth.onAuthStateChanged === 'function') {
      // Listen for Firebase Auth changes
      auth.onAuthStateChanged(async (firebaseUser: any) => {
        if (firebaseUser) {
          try {
            const token = await firebaseUser.getIdToken();
            console.log('Got Firebase token');

            // Always fetch fresh user data from backend for auth state
            // Don't rely on cache for authentication decisions
            let userData: User | null = null;
            let driverData: Driver | null = null;

            try {
              // Fetch fresh user data from backend
              const response = await api.get('/auth/me');
              userData = response.data as User;

              // Also cache the fresh data for faster UI updates later
              if (userData) {
                await appCache.set(CACHE_KEYS.USER_PROFILE, userData, CACHE_CONFIG.USER_PROFILE_TTL);
              }

              if (userData?.is_driver || userData?.role === 'driver') {
                try {
                  const driverRes = await api.get('/drivers/me');
                  driverData = driverRes.data as Driver;
                  await appCache.set(CACHE_KEYS.DRIVER_PROFILE, driverData, CACHE_CONFIG.USER_PROFILE_TTL);
                } catch (e) {
                  console.log('Failed to fetch driver data on init');
                }
              }

              set({
                user: userData,
                driver: driverData,
                token,
                isInitialized: true,
                isLoading: false
              });

              await storage.setItem('auth_token', token);

            } catch (err) {
              console.log('Failed to fetch user profile, but have valid firebase token');
              // If backend fails but we have firebase token, we might still want to let them in
              // or handle it gracefully. For now, we'll try to use cached data as fallback if backend is down
              const cachedUser = await appCache.get<User>(CACHE_KEYS.USER_PROFILE);
              if (cachedUser) {
                set({
                  user: cachedUser,
                  driver: await appCache.get<Driver>(CACHE_KEYS.DRIVER_PROFILE),
                  token,
                  isInitialized: true,
                  isLoading: false
                });
              } else {
                throw err; // Re-throw to trigger error state
              }
            }

          } catch (error: any) {
            console.log('Failed to sync user with backend:', error);
            set({ isLoading: false, isInitialized: true, error: 'Failed to sync user' });
          }
        } else {
          // No firebase user, but check if we have a stored token before giving up
          const storedToken = await storage.getItem('auth_token');
          if (storedToken) {
            console.log('No Firebase user, but found stored token. Attempting to restore session...');
            // We'll let the "fallback" block below handle this if we weren't using onAuthStateChanged
            // But since we are, we can verify the token here or just wait.
            // Actually, if onAuthStateChanged fires with null, it means Firebase explicitly thinks we are logged out.
            // However, sometimes it takes a moment.

            // If we rely purely on Firebase, then null means logged out.
            // But if we support custom backend tokens, we should check them.
          }

          console.log('No user logged in (state changed)');
          // Only clear if we really don't have a token in storage or if firebase explicitly signed out
          // For now, consistent behavior:
          await storage.deleteItem('auth_token');
          await appCache.clearUserCache();
          set({
            user: null,
            driver: null,
            token: null,
            isInitialized: true,
            isLoading: false
          });
        }
      });
    } else {
      // Firebase not configured — fall back to stored JWT token
      console.log('Firebase not configured, using stored token auth');
      const storedToken = await storage.getItem('auth_token');

      if (storedToken) {
        try {
          // Always fetch fresh user data for auth state
          const response = await api.get('/auth/me', {
            headers: { Authorization: `Bearer ${storedToken}` }
          });
          const userData = response.data as User;

          // Cache the fresh data for faster subsequent loads
          await appCache.set(CACHE_KEYS.USER_PROFILE, userData, CACHE_CONFIG.USER_PROFILE_TTL);

          let driverData: Driver | null = null;
          if (userData.is_driver || userData.role === 'driver') {
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

          set({
            user: userData,
            driver: driverData,
            token: storedToken,
            isInitialized: true,
            isLoading: false
          });
        } catch (error: any) {
          // If token is invalid, try to use cached data as a last resort before logging out?
          // No, if token is invalid, we must log out.
          console.log('Stored token invalid or expired:', error.message);
          await storage.deleteItem('auth_token');
          set({ user: null, driver: null, token: null, isInitialized: true, isLoading: false });
        }
      } else {
        console.log('No stored token found');
        set({ user: null, driver: null, token: null, isInitialized: true, isLoading: false });
      }
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
    try {
      await api.post(`/drivers/status?is_online=${isOnline}`);
      const driver = get().driver;
      if (driver) {
        set({ driver: { ...driver, is_online: isOnline } });
      }
    } catch (error) {
      console.log('Failed to update status');
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

      const response = await api.put('/users/profile-image', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
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
