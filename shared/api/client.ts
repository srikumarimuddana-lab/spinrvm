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

// ── In-memory token ──
// SecureStore can be unreliable on some devices/emulators (writes succeed but
// reads return null in the same session). We keep a module-level copy so that
// all API calls within the current app session have instant access to the
// token regardless of SecureStore's state.
let _inMemoryToken: string | null = null;

export function setInMemoryToken(token: string | null) {
  _inMemoryToken = token;
  console.log('[API] In-memory token:', token ? 'SET' : 'CLEARED');
}

// Helper to get stored token
const getStoredToken = async (): Promise<string | null> => {
  try {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      return localStorage.getItem('auth_token');
    } else {
      const SecureStore = require('expo-secure-store');
      const token = await SecureStore.getItemAsync('auth_token');
      return token;
    }
  } catch (e: any) {
    console.error('[API] SecureStore error:', e?.message || e);
    return null;
  }
};

// Helper to get auth header — checks in-memory first, then SecureStore
const getAuthHeader = async (): Promise<string | null> => {
  try {
    // 1. In-memory token (most reliable — set during current session)
    if (_inMemoryToken) {
      return _inMemoryToken;
    }
    // 2. Firebase token
    if (isFirebaseConfigured && auth.currentUser) {
      return await auth.currentUser.getIdToken();
    }
    // 3. SecureStore fallback (for cold starts where in-memory is empty)
    return await getStoredToken();
  } catch (error: any) {
    console.error('[API] Error getting auth token:', error?.message || error);
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
      console.log('DEBUG: Adding auth header for GET', url, 'Token starts with:', token.substring(0, 20));
    } else {
      console.log('DEBUG: NO TOKEN for GET', url);
    }

    const response = await fetchWithTimeout(`${API_URL}/api/v1${url}`, {
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

    const response = await fetchWithTimeout(`${API_URL}/api/v1${url}`, {
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
    const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
    const headers: Record<string, string> = {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...config?.headers,
    };
    // Strip any Content-Type for FormData so fetch can set the multipart boundary itself.
    if (isFormData) {
      delete headers['Content-Type'];
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(`${API_URL}/api/v1${url}`, {
      method: 'PUT',
      headers,
      body: body === undefined || body === null ? undefined : (isFormData ? body : JSON.stringify(body)),
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

    const response = await fetch(`${API_URL}/api/v1${url}`, {
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

    const response = await fetch(`${API_URL}/api/v1${url}`, {
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
