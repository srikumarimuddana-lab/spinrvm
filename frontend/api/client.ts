import axios from 'axios';
import Constants from 'expo-constants';
import { auth } from '../config/firebaseConfig';
import { Platform } from 'react-native';

const isFirebaseConfigured = typeof auth.onAuthStateChanged === 'function';

const getBackendUrl = () => {
  // 1. Prefer Environment Variable (e.g. from Vercel or .env)
  if (process.env.EXPO_PUBLIC_BACKEND_URL) {
    return process.env.EXPO_PUBLIC_BACKEND_URL;
  }

  // 2. Fallback for Expo Go (Local Development)
  // Expo Go runs on a physical device, so "localhost" won't work.
  // We need the IP of the machine running the Expo server.
  if (Constants.expoConfig?.hostUri) {
    const host = Constants.expoConfig.hostUri.split(':')[0];
    return `http://${host}:8000`;
  }

  // 3. Fallback for Web (Localhost)
  if (Platform.OS === 'web' && typeof window !== 'undefined') {
    // If no env var is set, assume local backend
    return 'http://localhost:8000';
  }

  return '';
};

const API_URL = getBackendUrl();

console.log('API Client configured with URL:', API_URL);

const client = axios.create({
  baseURL: `${API_URL}/api`,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Helper to get stored token
const getStoredToken = async (): Promise<string | null> => {
  try {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      return localStorage.getItem('auth_token');
    } else {
      const SecureStore = require('expo-secure-store');
      return await SecureStore.getItemAsync('auth_token');
    }
  } catch (e) { }
  return null;
};

client.interceptors.request.use(
  async (config) => {
    try {
      if (isFirebaseConfigured && auth.currentUser) {
        // Firebase token flow
        const token = await auth.currentUser.getIdToken();
        config.headers.Authorization = `Bearer ${token}`;
      } else {
        // Backend JWT flow — use stored token
        const storedToken = await getStoredToken();
        if (storedToken) {
          config.headers.Authorization = `Bearer ${storedToken}`;
        }
      }
    } catch (error) {
      console.error('Error attaching auth token:', error);
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

export default client;
