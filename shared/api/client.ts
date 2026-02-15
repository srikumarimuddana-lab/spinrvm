import { Platform } from 'react-native';
import { auth } from '../config/firebaseConfig';
import SpinrConfig from '../config/spinr.config';

const isFirebaseConfigured = typeof auth.onAuthStateChanged === 'function';

const API_URL = SpinrConfig.backendUrl;

console.log('API Client configured with URL:', API_URL);

// Request timeout in milliseconds
const REQUEST_TIMEOUT = 15000;

// Helper function to wrap fetch with timeout
const fetchWithTimeout = async (
  url: string,
  options: RequestInit & { timeout?: number } = {}
): Promise<Response> => {
  const { timeout = REQUEST_TIMEOUT, ...fetchOptions } = options;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    return response;
  } catch (error: any) {
    clearTimeout(timeoutId);
    if (error.name === 'AbortError') {
      const timeoutError: any = new Error('Network request timed out');
      timeoutError.name = 'TimeoutError';
      throw timeoutError;
    }
    throw error;
  }
};

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

    const response = await fetchWithTimeout(`${API_URL}/api${url}`, {
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

    const response = await fetchWithTimeout(`${API_URL}/api${url}`, {
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
