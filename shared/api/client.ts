import { Platform } from 'react-native';
import { auth } from '../config/firebaseConfig';
import SpinrConfig from '../config/spinr.config';

const isFirebaseConfigured = typeof auth.onAuthStateChanged === 'function';

const API_URL = SpinrConfig.backendUrl;

if (__DEV__) console.log('API Client configured with URL:', API_URL);

// Request timeout in milliseconds
const REQUEST_TIMEOUT = 15000;

// Propagate the client's timeout to the backend as an absolute epoch-ms
// deadline. The backend's DeadlineMiddleware reads this and uses it to
// skip DB retries once the client has already given up — frees backend
// thread-pool workers for requests that aren't already doomed.
function deadlineHeader(timeoutMs: number = REQUEST_TIMEOUT): Record<string, string> {
  return { 'X-Deadline-Ms': String(Date.now() + timeoutMs) };
}

// Generate a UUID v4 that works in React Native (no crypto.randomUUID on RN).
// The backend's RequestIDMiddleware echoes this back in X-Request-ID, so
// caller-generated IDs link client-side logs to backend loguru JSON lines.
function generateRequestId(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

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
  } catch (error: unknown) {
    clearTimeout(timeoutId);
    if (error instanceof Error && error.name === 'AbortError') {
      const timeoutError = new Error('Network request timed out');
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
  if (__DEV__) console.log('[API] In-memory token:', token ? 'SET' : 'CLEARED');
}

// ── CSRF double-submit token ──
// Populated from the `csrf_token` field in AuthResponse after every
// login/refresh. Sent as X-CSRF-Token on all state-changing requests.
// The backend validates it against the csrf_token cookie; the cookie is
// SameSite=Strict so cross-site requests can never present a matching pair.
let _csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  _csrfToken = token;
}

// ── Token refresh callback ──
// The auth store registers a refresh function here during initialization.
// This avoids a circular import between client.ts ↔ authStore.ts.
// On a 401, the client calls this once; if it returns true, the original
// request is retried with the newly-stored in-memory token.
type RefreshFn = () => Promise<boolean>;
let _refreshCallback: RefreshFn | null = null;
let _refreshPromise: Promise<boolean> | null = null;

export function setRefreshCallback(fn: RefreshFn): void {
  _refreshCallback = fn;
}

// Helper to get stored token
const getStoredToken = async (): Promise<string | null> => {
  try {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
      return sessionStorage.getItem('auth_token');
    } else {
      const SecureStore = require('expo-secure-store');
      const token = await SecureStore.getItemAsync('auth_token');
      return token;
    }
  } catch (e: unknown) {
    console.error('[API] SecureStore error:', e instanceof Error ? e.message : e);
    return null;
  }
};

// Helper to get auth header — checks in-memory first, then SecureStore.
// Exported so raw-fetch callers (e.g. multipart uploads in documents.tsx)
// can reuse the exact same resolution order instead of reading SecureStore
// directly. The access token is memory-only per authStore.setTokens, so a
// SecureStore-only lookup always returns null and produces 401s.
export const getAuthHeader = async (): Promise<string | null> => {
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
  } catch (error: unknown) {
    console.error('[API] Error getting auth token:', error instanceof Error ? error.message : error);
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
/** Loosely-typed shape of the error body the FastAPI backend can return. */
export interface ApiErrorBody {
  detail?: string | Array<{ msg?: string; loc?: unknown[]; type?: string } | string>;
  error?: {
    message?: string;
    detail?: string;
    request_id?: string;
    exception_type?: string;
    code?: number;
    message_key?: string;
    action_hint?: string;
  };
  retry_after?: number;
  limit?: number;
}

/**
 * Phase 2B: structured representation of a backend error.
 *
 * Backend Phase 2A emits `error.code`, `error.message_key`, and
 * `error.action_hint`. This client falls back gracefully to the
 * existing English `message` field when those new fields are absent
 * (i.e. when Phase 2A has not yet shipped).
 */
export interface ExtractedError {
  /** Numeric ErrorCode value, or 0 if unknown */
  code: number;
  /** English fallback message, always populated */
  message: string;
  /** i18n lookup key, e.g. "errors.auth.otp_invalid". Undefined for legacy/raw errors */
  messageKey?: string;
  /** Short user-action hint from backend, plain English */
  actionHint?: string;
  /** Backend-provided request ID for support tickets */
  requestId?: string;
  /** HTTP status code */
  status?: number;
  /** For 429 only */
  retryAfterSeconds?: number;
}

/**
 * Extract a structured error from any of the backend error response
 * shapes. Preserves the previous fallback ladder (FastAPI plain
 * `detail`, validation array, `error.message`, `error.detail`) and
 * additionally surfaces Phase 2A fields when present.
 */
export const extractError = (
  data: ApiErrorBody | null | undefined,
  status?: number,
): ExtractedError => {
  // Default skeleton — populated below.
  const result: ExtractedError = {
    code: 0,
    message: 'Request failed',
    status,
  };

  if (!data) return result;

  // Plain FastAPI HTTPException shape: detail is a string
  if (typeof data.detail === 'string') {
    result.message = data.detail;
  } else if (Array.isArray(data.detail)) {
    // RequestValidationError: detail is an array of {loc, msg, type}
    result.message = data.detail
      .map((d) => (typeof d === 'string' ? d : (d as { msg?: string })?.msg || JSON.stringify(d)))
      .join('; ');
  } else if (data.error?.message) {
    result.message = data.error.message;
  } else if (data.error?.detail) {
    result.message = data.error.detail;
  }

  // Phase 2A structured fields (gracefully absent on legacy responses).
  if (data.error) {
    if (typeof data.error.code === 'number') result.code = data.error.code;
    if (data.error.message_key) result.messageKey = data.error.message_key;
    if (data.error.action_hint) result.actionHint = data.error.action_hint;
    if (data.error.request_id) result.requestId = data.error.request_id;
  }

  return result;
};

/**
 * Backwards-compatible string extractor — every existing caller of
 * `extractErrorMessage` continues to work unchanged.
 */
const extractErrorMessage = (data: ApiErrorBody | null | undefined): string =>
  extractError(data).message;

// ─── B-P1-8: typed rate-limit error ────────────────────────────────────
// Backend (utils/rate_limiter.py + routes/auth.py::_check_otp_lockout)
// emits 429 with:
//   Retry-After: <seconds>           — RFC 9110 (integer seconds form)
//   RateLimit-Limit: <amount>        — IETF draft-ietf-httpapi-ratelimit-headers
//   RateLimit-Remaining: 0
//   RateLimit-Reset: <seconds>
// We surface those as a typed error so callers (login forms, "sign out
// of all devices" buttons, OTP screens) can show "try again in 60s"
// UX instead of a generic "Request failed". We DO NOT auto-retry —
// rate-limited POSTs may have side effects, and even safe GETs would
// just block the spinner for the window. The caller decides.
export class RateLimitError extends Error {
  status = 429 as const;
  retryAfterSeconds: number;
  limit: number | null;
  remaining: number | null;
  resetSeconds: number | null;
  data: ApiErrorBody;
  requestId?: string;

  constructor(opts: {
    message: string;
    retryAfterSeconds: number;
    limit: number | null;
    remaining: number | null;
    resetSeconds: number | null;
    data: ApiErrorBody;
    requestId?: string;
  }) {
    super(opts.message);
    this.name = 'RateLimitError';
    this.retryAfterSeconds = opts.retryAfterSeconds;
    this.limit = opts.limit;
    this.remaining = opts.remaining;
    this.resetSeconds = opts.resetSeconds;
    this.data = opts.data;
    this.requestId = opts.requestId;
  }
}

/**
 * Phase 2B: typed error class for any non-429 backend failure. Carries
 * the structured fields from `ExtractedError` so callers (alert helper,
 * screens) can resolve the i18n key without re-parsing the body.
 *
 * 429s remain `RateLimitError` for source-compatibility with existing
 * `instanceof RateLimitError` checks at OTP/login screens.
 */
export class SpinrApiError extends Error {
  status: number;
  code: number;
  messageKey?: string;
  actionHint?: string;
  requestId?: string;
  data: ApiErrorBody;
  // Mirror the pre-Phase-2B `error.response` shape so that callers
  // doing `error.response.data` / `error.response.status` keep working.
  response: { data: ApiErrorBody; status: number };

  constructor(extracted: ExtractedError, data: ApiErrorBody) {
    super(extracted.message);
    this.name = 'SpinrApiError';
    this.status = extracted.status ?? 0;
    this.code = extracted.code;
    this.messageKey = extracted.messageKey;
    this.actionHint = extracted.actionHint;
    this.requestId = extracted.requestId;
    this.data = data;
    this.response = { data, status: this.status };
  }
}

// Parse a Retry-After header per RFC 9110 §10.2.3:
//   - integer seconds (delta-seconds form)         → preferred
//   - HTTP-date (e.g. "Fri, 31 Dec 2025 23:59:59 GMT")
// Returns the delay in seconds, or `fallback` if absent/malformed.
// Negative values are clamped to 0 (servers occasionally send "0" to
// mean "retry immediately"; a negative would be a bug we shouldn't
// propagate as a sleep duration).
const parseRetryAfter = (header: string | null, fallback: number): number => {
  if (!header) return fallback;
  const trimmed = header.trim();
  // delta-seconds form
  if (/^\d+$/.test(trimmed)) {
    const seconds = parseInt(trimmed, 10);
    return Number.isFinite(seconds) ? Math.max(seconds, 0) : fallback;
  }
  // HTTP-date form
  const dateMs = Date.parse(trimmed);
  if (Number.isFinite(dateMs)) {
    const deltaSeconds = Math.ceil((dateMs - Date.now()) / 1000);
    return Math.max(deltaSeconds, 0);
  }
  return fallback;
};

const parseIntHeader = (header: string | null): number | null => {
  if (!header) return null;
  const n = parseInt(header.trim(), 10);
  return Number.isFinite(n) ? n : null;
};

// In-memory ring buffer of recent API errors so we can surface them in a
// debug screen (or just logcat them) without the user having to reproduce
// on-device with Metro attached. Capped at MAX_ERROR_LOG entries.
//
// Phase 4 (P1-7): added optional `surface` + `screen` tags so support can
// tell which surface (rider-app/driver-app/admin-dashboard) and which
// screen-level flow ("ride-options", "payment-confirm", …) produced the
// failure without asking the user to reproduce. Both fields are optional
// — call sites are migrated incrementally.
export interface ApiErrorLogEntry {
  ts: string;
  method: string;
  url: string;
  status: number;
  message: string;
  request_id?: string;
  exception_type?: string;
  data?: ApiErrorBody;
  surface?: 'rider-app' | 'driver-app' | 'admin-dashboard' | 'shared';
  screen?: string;
}
const _errorLog: ApiErrorLogEntry[] = [];
const MAX_ERROR_LOG = 500;

// Default surface tag, set once at app startup via `setApiErrorSurface`.
// Used as a fallback when individual call sites don't pass `surface`
// explicitly. Lets us avoid touching every recordApiError caller in this
// PR — downstream PRs migrate per-screen `screen` tags incrementally.
let _defaultSurface: ApiErrorLogEntry['surface'] = undefined;
export const setApiErrorSurface = (surface: ApiErrorLogEntry['surface']): void => {
  _defaultSurface = surface;
};

export const getApiErrorLog = (): ApiErrorLogEntry[] => [..._errorLog];
export const clearApiErrorLog = (): void => { _errorLog.length = 0; };
const recordApiError = (entry: ApiErrorLogEntry) => {
  if (entry.surface === undefined && _defaultSurface !== undefined) {
    entry.surface = _defaultSurface;
  }
  _errorLog.push(entry);
  if (_errorLog.length > MAX_ERROR_LOG) _errorLog.shift();
  // Also console.log so it shows up in Metro / Railway mirror. Tagged so
  // it's easy to grep. Keep this concise — full data is in the buffer.
  console.log(
    `[API-ERR] ${entry.method} ${entry.url} → ${entry.status} | ${entry.message}` +
    (entry.request_id ? ` | req=${entry.request_id}` : '') +
    (entry.surface ? ` | surface=${entry.surface}` : '') +
    (entry.screen ? ` | screen=${entry.screen}` : ''),
  );
};

const handleApiError = async (response: Response, method: string, url: string, retryFn?: () => Promise<never>): Promise<never> => {
  // On 401, attempt a single silent token refresh then retry the original request.
  if (response.status === 401 && _refreshCallback && retryFn && url !== '/auth/refresh') {
    try {
      // Deduplicate concurrent refresh calls — only one in-flight at a time.
      if (!_refreshPromise) {
        _refreshPromise = _refreshCallback().finally(() => {
          _refreshPromise = null;
        });
      }
      const refreshed = await _refreshPromise;
      if (refreshed) {
        return retryFn(); // retry with the new token now in _inMemoryToken
      }
    } catch {
      // refresh failed — fall through to throw the original 401
    }
  }

  // On 503 (Supabase transient — see db_supabase.run_sync), retry once
  // after a short delay before surfacing the error. The backend's
  // run_sync already does its own 2-retry exponential-backoff loop, so
  // a 503 here means the burst outlasted ~2s of retries server-side.
  // Giving the rider-app one more shot after another 1.5s catches
  // longer Supabase edge hiccups without the user seeing
  // "Service temporarily unavailable: database" mid-booking.
  if (response.status === 503 && retryFn && url !== '/auth/refresh') {
    await new Promise(r => setTimeout(r, 1500));
    try {
      return retryFn();
    } catch {
      // retry threw — fall through to the original error surface below
    }
  }

  const errorData = await response.json().catch(() => ({}));
  const extracted = extractError(errorData, response.status);
  const message = extracted.message;
  const requestId = response.headers.get('x-request-id') || extracted.requestId;
  const exceptionType = errorData?.error?.exception_type;
  // Ensure the structured object reflects the header request ID (which
  // wins over the body) so downstream consumers see a single source.
  if (requestId) extracted.requestId = requestId;
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

  // B-P1-8: surface 429s as a typed RateLimitError so login/OTP/logout
  // screens can show "try again in N seconds" instead of falling into
  // the generic "Request failed" path. Body fallback: if the header
  // is missing for some upstream reason (proxy strip, etc.) we still
  // have errorData.retry_after / limit from the JSON body.
  if (response.status === 429) {
    const retryAfterSeconds = parseRetryAfter(
      response.headers.get('Retry-After'),
      typeof errorData?.retry_after === 'number' ? errorData.retry_after : 60,
    );
    const limit =
      parseIntHeader(response.headers.get('RateLimit-Limit')) ??
      (typeof errorData?.limit === 'number' ? errorData.limit : null);
    const remaining = parseIntHeader(response.headers.get('RateLimit-Remaining'));
    const resetSeconds = parseIntHeader(response.headers.get('RateLimit-Reset'));
    throw new RateLimitError({
      message,
      retryAfterSeconds,
      limit,
      remaining,
      resetSeconds,
      data: errorData,
      requestId: requestId || undefined,
    });
  }

  // ── G2: Catch expired / invalid tokens globally ──────────────
  // If the backend returns 401, the JWT has expired or been revoked.
  // Clear the in-memory token and the persisted auth state so the
  // Zustand auth store flips `isAuthenticated` to false — which
  // triggers the layout's redirect-to-login effect in both apps.
  // This prevents the "session limbo" state where API calls silently
  // fail 401 while the driver/rider still sees the dashboard.
  if (response.status === 401) {
    console.log('[API] 401 Unauthorized — clearing session');
    setInMemoryToken(null);
    try {
      if (Platform.OS === 'web' && typeof window !== 'undefined') {
        localStorage.removeItem('auth_token');
      } else {
        const SecureStore = require('expo-secure-store');
        await SecureStore.deleteItemAsync('auth_token');
      }
    } catch { /* best-effort clear */ }

    // Lazily import the auth store to avoid circular deps. The store's
    // logout() clears user/token/isAuthenticated — the layout effects
    // in both apps watch isAuthenticated and redirect to /login.
    try {
      const { useAuthStore } = require('../store/authStore');
      useAuthStore.getState().logout();
    } catch { /* store may not be initialized yet on cold start */ }
  }

  // Phase 2B: throw SpinrApiError so consumers can read messageKey,
  // actionHint, and code. The class also exposes the legacy
  // `response` / `requestId` shape so existing callers that read
  // `err.response.data` or `err.requestId` keep working unchanged.
  throw new SpinrApiError(extracted, errorData);
};

// Custom API client using fetch
const client = {
  async get<T = unknown>(url: string, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Request-ID': generateRequestId(),
      ...deadlineHeader(),
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetchWithTimeout(`${API_URL}/api/v1${url}`, {
      method: 'GET',
      headers,
    });

    if (!response.ok) await handleApiError(response, 'GET', url, () => client.get(url, config));

    const data = await response.json();
    return { data, status: response.status };
  },

  async post<T = unknown>(url: string, body?: unknown, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Request-ID': generateRequestId(),
      ...deadlineHeader(),
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (_csrfToken) {
      headers['X-CSRF-Token'] = _csrfToken;
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

  async put<T = unknown>(url: string, body?: unknown, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
    const headers: Record<string, string> = {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      'X-Request-ID': generateRequestId(),
      ...config?.headers,
    };
    // Strip any Content-Type for FormData so fetch can set the multipart boundary itself.
    if (isFormData) {
      delete headers['Content-Type'];
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (_csrfToken) {
      headers['X-CSRF-Token'] = _csrfToken;
    }

    const response = await fetchWithTimeout(`${API_URL}/api/v1${url}`, {
      method: 'PUT',
      headers,
      body: body === undefined || body === null ? undefined : (isFormData ? body : JSON.stringify(body)),
    });

    if (!response.ok) await handleApiError(response, 'PUT', url, () => client.put(url, body, config));

    const data = await response.json();
    return { data, status: response.status };
  },

  async patch<T = unknown>(url: string, body?: unknown, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Request-ID': generateRequestId(),
      ...deadlineHeader(),
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (_csrfToken) {
      headers['X-CSRF-Token'] = _csrfToken;
    }

    const response = await fetchWithTimeout(`${API_URL}/api/v1${url}`, {
      method: 'PATCH',
      headers,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) await handleApiError(response, 'PATCH', url, () => client.patch(url, body, config));

    const data = await response.json();
    return { data, status: response.status };
  },

  async delete<T = unknown>(url: string, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Request-ID': generateRequestId(),
      ...deadlineHeader(),
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    if (_csrfToken) {
      headers['X-CSRF-Token'] = _csrfToken;
    }

    const response = await fetchWithTimeout(`${API_URL}/api/v1${url}`, {
      method: 'DELETE',
      headers,
    });

    if (!response.ok) await handleApiError(response, 'DELETE', url, () => client.delete(url, config));

    const data = await response.json().catch(() => ({} as T));
    return { data, status: response.status };
  },
};

export default client;
