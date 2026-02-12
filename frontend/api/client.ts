import axios from 'axios';
import Constants from 'expo-constants';
import { auth } from '../config/firebaseConfig';
import { Platform } from 'react-native';

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

client.interceptors.request.use(
  async (config) => {
    try {
      const user = auth.currentUser;
      if (user) {
        // Force refresh to ensure valid token
        const token = await user.getIdToken();
        config.headers.Authorization = `Bearer ${token}`;
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
