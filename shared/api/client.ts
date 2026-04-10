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

// ── Token refresh callback ──
// The auth store registers a refresh function here during initialization.
// This avoids a circular import between client.ts ↔ authStore.ts.
// On a 401, the client calls this once; if it returns true, the original
// request is retried with the newly-stored in-memory token.
type RefreshFn = () => Promise<boolean>;
let _refreshCallback: RefreshFn | null = null;
let _isRefreshing = false;
let _refreshPromise: Promise<boolean> | null = null;

export function setRefreshCallback(fn: RefreshFn): void {
  _refreshCallback = fn;
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

// ─── Error extraction + debug log ring buffer ────────────────────────
// The backend returns errors in a few shapes depending on the handler:
//   - HTTPException (via http_exception_handler): { detail, error:{message} }
//   - Unhandled Exception (via general_exception_handler): { error:{message,detail,request_id,exception_type} }
//   - FastAPI RequestValidationError: { detail: [...] }  (array of field errors)
//   - Plain text "Internal Server Error" (pre-handler-register crash): body not JSON
// This helper returns a human-readable message from any of them.
const extractErrorMessage = (data: any): string => {
  if (!data) return 'Request failed';
  // Plain FastAPI HTTPException shape: detail is a string
  if (typeof data.detail === 'string') return data.detail;
  // RequestValidationError: detail is an array of {loc, msg, type}
  if (Array.isArray(data.detail)) {
    return data.detail
      .map((d: any) => (typeof d === 'string' ? d : d?.msg || JSON.stringify(d)))
      .join('; ');
  }
  // Our custom handlers: structured error object
  if (data.error?.message) return data.error.message;
  if (data.error?.detail) return data.error.detail;
  return 'Request failed';
};

// In-memory ring buffer of recent API errors so we can surface them in a
// debug screen (or just logcat them) without the user having to reproduce
// on-device with Metro attached. Capped at 50 entries.
export interface ApiErrorLogEntry {
  ts: string;
  method: string;
  url: string;
  status: number;
  message: string;
  request_id?: string;
  exception_type?: string;
  data?: any;
}
const _errorLog: ApiErrorLogEntry[] = [];
const MAX_ERROR_LOG = 50;
export const getApiErrorLog = (): ApiErrorLogEntry[] => [..._errorLog];
export const clearApiErrorLog = (): void => { _errorLog.length = 0; };
const recordApiError = (entry: ApiErrorLogEntry) => {
  _errorLog.push(entry);
  if (_errorLog.length > MAX_ERROR_LOG) _errorLog.shift();
  // Also console.log so it shows up in Metro / Railway mirror. Tagged so
  // it's easy to grep. Keep this concise — full data is in the buffer.
  console.log(
    `[API-ERR] ${entry.method} ${entry.url} → ${entry.status} | ${entry.message}` +
    (entry.request_id ? ` | req=${entry.request_id}` : ''),
  );
};

const handleApiError = async (response: Response, method: string, url: string, retryFn?: () => Promise<any>): Promise<never> => {
  // On 401, attempt a single silent token refresh then retry the original request.
  if (response.status === 401 && _refreshCallback && retryFn && url !== '/auth/refresh') {
    try {
      // Deduplicate concurrent refresh calls — only one in-flight at a time.
      if (!_isRefreshing) {
        _isRefreshing = true;
        _refreshPromise = _refreshCallback().finally(() => {
          _isRefreshing = false;
          _refreshPromise = null;
        });
      }
      const refreshed = await _refreshPromise!;
      if (refreshed) {
        return retryFn() as any; // retry with the new token now in _inMemoryToken
      }
    } catch {
      // refresh failed — fall through to throw the original 401
    }
  }

  const errorData = await response.json().catch(() => ({}));
  const message = extractErrorMessage(errorData);
  const requestId = response.headers.get('x-request-id') || errorData?.error?.request_id;
  const exceptionType = errorData?.error?.exception_type;
  recordApiError({
    ts: new Date().toISOString(),
    method,
    url,
    status: response.status,
    message,
    request_id: requestId || undefined,
    exception_type: exceptionType,
    data: errorData,
  });
  const error: any = new Error(message);
  error.response = { data: errorData, status: response.status };
  error.requestId = requestId;
  throw error;
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

    if (!response.ok) await handleApiError(response, 'GET', url, () => client.get(url, config));

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

    if (!response.ok) await handleApiError(response, 'POST', url, () => client.post(url, body, config));

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

    if (!response.ok) await handleApiError(response, 'PUT', url, () => client.put(url, body, config));

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

    if (!response.ok) await handleApiError(response, 'PATCH', url, () => client.patch(url, body, config));

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

    if (!response.ok) await handleApiError(response, 'DELETE', url, () => client.delete(url, config));

    const data = await response.json().catch(() => ({} as T));
    return { data, status: response.status };
  },
};

export default client;
