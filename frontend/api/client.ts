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

// Helper to get auth header
const getAuthHeader = async (): Promise<string | null> => {
  try {
    if (isFirebaseConfigured && auth.currentUser) {
      // Firebase token flow
      return await auth.currentUser.getIdToken();
    } else {
      // Backend JWT flow — use stored token
      return await getStoredToken();
    }
  } catch (error) {
    console.error('Error getting auth token:', error);
    return null;
  }
};

// Custom API client using fetch
const client = {
  async get<T = any>(url: string, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_URL}/api${url}`, {
      method: 'GET',
      headers,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
      const error: any = new Error(errorData.detail || 'Request failed');
      error.response = { data: errorData, status: response.status };
      throw error;
    }

    const data = await response.json();
    return { data, status: response.status };
  },

  async post<T = any>(url: string, body?: any, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_URL}/api${url}`, {
      method: 'POST',
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
      const error: any = new Error(errorData.detail || 'Request failed');
      error.response = { data: errorData, status: response.status };
      throw error;
    }

    const data = await response.json();
    return { data, status: response.status };
  },

  async put<T = any>(url: string, body?: any, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_URL}/api${url}`, {
      method: 'PUT',
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
      const error: any = new Error(errorData.detail || 'Request failed');
      error.response = { data: errorData, status: response.status };
      throw error;
    }

    const data = await response.json();
    return { data, status: response.status };
  },

  async patch<T = any>(url: string, body?: any, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_URL}/api${url}`, {
      method: 'PATCH',
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
      const error: any = new Error(errorData.detail || 'Request failed');
      error.response = { data: errorData, status: response.status };
      throw error;
    }

    const data = await response.json();
    return { data, status: response.status };
  },

  async delete<T = any>(url: string, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_URL}/api${url}`, {
      method: 'DELETE',
      headers,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
      const error: any = new Error(errorData.detail || 'Request failed');
      error.response = { data: errorData, status: response.status };
      throw error;
    }

    const data = await response.json().catch(() => ({} as T));
    return { data, status: response.status };
  },
};

export default client;
