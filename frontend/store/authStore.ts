import { create } from 'zustand';
import * as SecureStore from 'expo-secure-store';
import axios from 'axios';
import Constants from 'expo-constants';
import { Platform } from 'react-native';

const API_URL = Constants.expoConfig?.extra?.backendUrl || process.env.EXPO_PUBLIC_BACKEND_URL || '';

console.log('API_URL configured:', API_URL);

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
  license_plate: string;
  rating: number;
  total_rides: number;
  is_online: boolean;
  is_available: boolean;
}

interface User {
  id: string;
  phone: string;
  first_name?: string;
  last_name?: string;
  email?: string;
  city?: string;
  role: string;
  created_at: string;
  profile_complete: boolean;
  is_driver?: boolean;
}

interface AuthState {
  user: User | null;
  token: string | null;
  isLoading: boolean;
  isInitialized: boolean;
  error: string | null;
  driver: Driver | null;
  isDriverMode: boolean;

  fetchDriverProfile: () => Promise<void>;
  registerDriver: (data: any) => Promise<void>;
  toggleDriverMode: () => void;
  updateDriverStatus: (isOnline: boolean) => Promise<void>;

  
  // Actions
  initialize: () => Promise<void>;
  sendOTP: (phone: string) => Promise<{ success: boolean; dev_otp?: string }>;
  verifyOTP: (phone: string, code: string) => Promise<{ is_new_user: boolean }>;
  createProfile: (data: { first_name: string; last_name: string; email: string; city: string }) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

const api = axios.create({
  baseURL: `${API_URL}/api`,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  driver: null,
  isDriverMode: false,
  token: null,
  isLoading: false,
  isInitialized: false,
  error: null,


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

  initialize: async () => {
    try {
      console.log('Auth initializing...');
      set({ isLoading: true });
      
      // Get token from secure storage
      const token = await storage.getItem('auth_token');
      console.log('Token from storage:', token ? 'exists' : 'none');
      
      if (token) {
        // Set token in axios headers
        api.defaults.headers.common['Authorization'] = `Bearer ${token}`;
        
        try {
          // Fetch user profile with timeout
          const response = await Promise.race([
            api.get('/auth/me'),
            new Promise((_, reject) => 
              setTimeout(() => reject(new Error('Timeout')), 5000)
            )
          ]) as any;
          
          console.log('User fetched successfully');

          const userData = response.data;
          let driverData = null;

          if (userData.is_driver || userData.role === 'driver') {
            try {
              const driverRes = await api.get('/drivers/me');
              driverData = driverRes.data;
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

        } catch (fetchError) {
          console.log('Failed to fetch user, clearing token');
          await storage.deleteItem('auth_token');
          set({ 
            user: null,
  driver: null,
  isDriverMode: false,
            token: null, 
            isInitialized: true, 
            isLoading: false 
          });
        }
      } else {
        console.log('No token, setting initialized');
        set({ isInitialized: true, isLoading: false });
      }
    } catch (error: any) {
      console.log('Auth initialization error:', error.message);
      // Token might be invalid, clear it
      await storage.deleteItem('auth_token');
      set({ 
        user: null,
  driver: null,
  isDriverMode: false,
        token: null, 
        isInitialized: true, 
        isLoading: false 
      });
    }
  },

  sendOTP: async (phone: string) => {
    try {
      set({ isLoading: true, error: null });
      
      const response = await api.post('/auth/send-otp', { phone });
      
      set({ isLoading: false });
      return { 
        success: response.data.success, 
        dev_otp: response.data.dev_otp 
      };
    } catch (error: any) {
      const message = error.response?.data?.detail || 'Failed to send OTP';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  verifyOTP: async (phone: string, code: string) => {
    try {
      set({ isLoading: true, error: null });
      
      const response = await api.post('/auth/verify-otp', { phone, code });
      const { token, user, is_new_user } = response.data;
      
      // Store token securely
      await storage.setItem('auth_token', token);
      
      // Set token in axios headers
      api.defaults.headers.common['Authorization'] = `Bearer ${token}`;
      
      set({ 
        user, 
        token, 
        isLoading: false 
      });
      
      return { is_new_user };
    } catch (error: any) {
      const message = error.response?.data?.detail || 'Invalid verification code';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  createProfile: async (data) => {
    try {
      set({ isLoading: true, error: null });
      
      const response = await api.post('/users/profile', data);
      
      set({ 
        user: response.data, 
        isLoading: false 
      });
    } catch (error: any) {
      const message = error.response?.data?.detail || 'Failed to create profile';
      set({ isLoading: false, error: message });
      throw new Error(message);
    }
  },

  logout: async () => {
    try {
      await storage.deleteItem('auth_token');
      delete api.defaults.headers.common['Authorization'];
      set({ user: null,
  driver: null,
  isDriverMode: false, token: null });
    } catch (error) {
      console.log('Logout error:', error);
    }
  },

  clearError: () => set({ error: null }),
}));
