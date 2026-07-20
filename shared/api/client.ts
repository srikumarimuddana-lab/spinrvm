import { Platform } from 'react-native';
import SpinrConfig from '../config/spinr.config';
import { clampToastMessage, TOAST_MESSAGE_MAX } from '../utils/toastMessage';


const API_URL = SpinrConfig.backendUrl;

// Resolved URL log is gated to dev builds — it's operational metadata that
// doesn't need to land in production device logs / crash reports. The
// local-fallback warning stays in all builds because a prod build pointed at
// localhost is a launch-breaking misconfig worth surfacing everywhere.
if (__DEV__) console.log('[API] Backend URL:', API_URL);
if (!API_URL || API_URL.includes('localhost') || API_URL.includes('10.0.2.2')) {
  console.warn(
    '[API] ⚠ Backend URL looks like a local dev fallback. ' +
    'If this is a production build, EXPO_PUBLIC_BACKEND_URL was not set during the EAS update.',
  );
}

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
    // Log the raw fetch failure — "Network request failed" at this layer
    // means the native networking stack couldn't send the request at all
    // (DNS, TLS, malformed URL, or cleartext-HTTP block on Android).
    console.error(
      `[API] fetch() failed: url=${url} error=${error instanceof Error ? error.message : String(error)}`,
    );
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

export function hasAuthToken(): boolean {
  return _inMemoryToken !== null;
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
// _refreshPromise deduplicates concurrent refresh calls: every in-flight
// request that hits 401 while a refresh is already running awaits the
// same promise instead of firing N parallel /auth/refresh requests.
let _refreshPromise: Promise<boolean> | null = null;
// Subscriber queue: requests that arrived while a refresh was in progress
// register here. When the refresh succeeds they are all retried with the
// newly-stored token rather than racing to start a second refresh.
let _refreshSubscribers: Array<(token: string) => void> = [];

function _subscribeTokenRefresh(cb: (token: string) => void): void {
  _refreshSubscribers.push(cb);
}

function _onRefreshed(token: string): void {
  _refreshSubscribers.forEach(cb => cb(token));
  _refreshSubscribers = [];
}

// Exposed so the auth store can sign the user out when the refresh token
// is itself rejected — prevents the client from entering a limbo state.
let _signOutCallback: (() => Promise<void>) | null = null;
export function setSignOutCallback(fn: () => Promise<void>): void {
  _signOutCallback = fn;
}

// While the auth layer (authStore.refreshTokens) runs its OWN controlled
// refresh attempt, a 401 from /auth/refresh must NOT trigger the hard
// sign-out below — the auth layer decides whether to retry with a
// fresher stored token (foreground/background refresh-token rotation race)
// or actually tear down the session. refreshTokens acquires this for the
// duration of that controlled attempt and releases it in a finally.
//
// Reference-counted, NOT a plain boolean: refreshTokens can be invoked
// concurrently from paths that don't share the interceptor's _refreshPromise
// dedup (e.g. a cold-start initialize() refresh racing a 401-triggered one).
// With a boolean, the first caller's `finally` reset would drop suppression
// while the second is still mid-retry. The counter keeps suppression active
// until the LAST controlled caller releases it.
let _suppressRefreshSignOutDepth = 0;
export function setSuppressRefreshSignOut(v: boolean): void {
  if (v) _suppressRefreshSignOutDepth++;
  else _suppressRefreshSignOutDepth = Math.max(0, _suppressRefreshSignOutDepth - 1);
}

export function setRefreshCallback(fn: RefreshFn): void {
  _refreshCallback = fn;
}

// ── Proactive token refresh ──
// Called before critical actions (AppState resume, WS connect, periodic timer)
// to ensure the access token has enough remaining lifetime. Avoids the
// 401 → refresh → retry delay that surfaces as a 1-2s stutter on actions
// like "Arrived at Pickup" after the app was backgrounded.
const TOKEN_REFRESH_BUFFER_MS = 2 * 60 * 1000; // refresh when <2 min remaining

export async function ensureFreshToken(): Promise<void> {
  if (!_refreshCallback) return;

  const { useAuthStore } = require('../store/authStore');
  const { tokenExpiresAt, token } = useAuthStore.getState();

  if (!token || !tokenExpiresAt) return;
  if (Date.now() < tokenExpiresAt - TOKEN_REFRESH_BUFFER_MS) return;

  // Reuse in-flight refresh from the 401 handler (or vice-versa).
  if (_refreshPromise) {
    try { await _refreshPromise; } catch {}
    return;
  }

  try {
    _refreshPromise = _refreshCallback();
    await _refreshPromise;
  } catch {
    // Proactive refresh failed — the reactive 401 handler will catch it.
  } finally {
    _refreshPromise = null;
  }
}

// ── Firebase App Check token injection ──────────────────────────────
// The mobile apps call setAppCheckTokenProvider() at startup after
// initializing Firebase so every API request carries X-Firebase-AppCheck.
// The provider is a zero-arg async fn that returns the token or null —
// null means "App Check not available on this device/env" and the header
// is simply omitted (backend enforcement decides whether to accept or reject).
let _appCheckTokenProvider: (() => Promise<string | null>) | null = null;

export function setAppCheckTokenProvider(fn: () => Promise<string | null>): void {
  _appCheckTokenProvider = fn;
}

async function appCheckHeader(): Promise<Record<string, string>> {
  if (!_appCheckTokenProvider) return {};
  try {
    const token = await _appCheckTokenProvider();
    return token ? { 'X-Firebase-AppCheck': token } : {};
  } catch {
    return {};
  }
}

/**
 * True if an App Check token is currently obtainable — or if App Check is not
 * wired into this build (e.g. dev, where backend enforcement is off anyway).
 *
 * Background pollers that hit App-Check-ENFORCED /api/v1 endpoints on a
 * focus/interval (e.g. the notification-badge poll) can gate on this to avoid
 * firing before Firebase App Check has minted a token — which would 401 under
 * enforcement and spam the backend logs. The poll simply skips that tick; its
 * next focus/interval run picks it up once the token is ready.
 */
export async function isAppCheckTokenReady(): Promise<boolean> {
  if (!_appCheckTokenProvider) return true; // App Check not configured here
  try {
    return (await _appCheckTokenProvider()) != null;
  } catch {
    return false;
  }
}

// ── Phase 4 (P1-9): outgoing W3C traceparent header ─────────────────
// Callers that have started a trace span (e.g. screen-level RUM) can
// register the active trace ID here; the request methods forward it as
// the W3C `traceparent` header so the backend can stitch its loguru
// request_id to the upstream client trace. Stub form: parent-id is
// zeroed and only the trace-id is meaningful — full OTel SDK
// integration is a follow-up. Set to `null` to disable.
let _outgoingTraceId: string | null = null;
export const setOutgoingTraceId = (id: string | null): void => {
  _outgoingTraceId = id;
};
const traceparentHeader = (): Record<string, string> => {
  if (!_outgoingTraceId) return {};
  // version "00", flags "01" (sampled). parent-id is the 16-hex
  // child-span; without an OTel SDK we don't have one yet, so zero it.
  return { traceparent: `00-${_outgoingTraceId}-0000000000000000-01` };
};

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
    // 2. SecureStore fallback (for cold starts where in-memory is empty)
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
    // SpinrException's `details` dict (route-supplied diagnostic payload).
    // Stripe error specificity uses `details.code === 'action_required'`
    // to flag 3-D Secure / SCA challenges that the rider SDK must drive
    // through `handleNextAction()`. Routes that don't need it omit the
    // field entirely.
    details?: Record<string, unknown> | null;
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
  /**
   * String discriminator from `error.details.code`. The Stripe error
   * specificity work uses this to flag 3-D Secure challenges as
   * `'action_required'` so the payment screen can drive
   * `handleNextAction()` instead of treating the 402 as a generic
   * decline. Other routes may introduce additional discriminators
   * (e.g. `'documents_pending'`, `'low_balance'`); callers branch on
   * the literal value.
   */
  detailCode?: string;
  /** Full `error.details` payload — surfaced verbatim for callers
   * that need fields beyond `detailCode` (e.g. the 3-D Secure
   * `client_secret` + `next_action` for `handleNextAction()`). */
  details?: Record<string, unknown>;
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
    // Surface `error.details` for callers that need to branch on a
    // string discriminator (e.g. `'action_required'` for 3-D Secure).
    // Backend writes the dict via `SpinrException(details=...)`.
    if (data.error.details && typeof data.error.details === 'object') {
      result.details = data.error.details as Record<string, unknown>;
      const detailCode = (data.error.details as { code?: unknown }).code;
      if (typeof detailCode === 'string') result.detailCode = detailCode;
    }
  }

  return result;
};

/**
 * Convenience wrapper over `extractError` for toast/alert call sites. Takes an
 * Axios error (or anything with `response.data`) and returns the backend's
 * specific human message (e.g. "This email is already linked to an existing
 * Spinr account") rather than Axios's generic "Request failed with status code
 * 400". Falls back to the caller's message only when the server sent no usable
 * detail (network error, empty body).
 */
// Toast length caps live in shared/utils/toastMessage.ts (dependency-free so
// the toast stores can enforce them without pulling in this client).
// Re-exported for the existing call/test sites that import them from
// '@shared/api/client'.
export { clampToastMessage, TOAST_MESSAGE_MAX };

export function getApiErrorMessage(
  err: unknown,
  fallback = 'Something went wrong. Please try again.',
): string {
  const anyErr = err as {
    name?: string;
    response?: { data?: ApiErrorBody | null; status?: number };
    message?: string;
    retryAfterSeconds?: number;
  } | null;
  // 429s are thrown as RateLimitError, which carries no `.response` — without
  // this branch a lockout would fall through to the generic fallback and read
  // like a connectivity problem. Prefer the backend's own message (e.g. the
  // OTP-lockout wording) and only synthesize one from Retry-After when the
  // body had no usable detail. Duck-typed by name so plain-object test
  // doubles and cross-realm instances match too.
  if (anyErr?.name === 'RateLimitError') {
    const raw = anyErr.message;
    if (raw && raw !== 'Request failed') return clampToastMessage(raw);
    const retryAfter = anyErr.retryAfterSeconds;
    if (typeof retryAfter === 'number' && retryAfter > 0) {
      return `Too many requests — please try again in ${retryAfter}s.`;
    }
    return 'Too many requests — please try again shortly.';
  }
  const data = anyErr?.response?.data;
  if (data) {
    const { message } = extractError(data, anyErr?.response?.status);
    // extractError returns the default 'Request failed' when the body had no
    // recognizable detail — treat that as "no useful message" and fall back.
    if (message && message !== 'Request failed') return clampToastMessage(message);
  }
  // No usable response body. Some callers (e.g. authStore.createProfile) extract
  // the backend detail themselves and re-throw `new Error(detail)`, so a
  // meaningful err.message should still win. Ignore Axios's generic
  // "Request failed with status code N" and "Network Error", and JSON-parse
  // SyntaxErrors from a malformed/HTML response body ("JSON Parse error: …"
  // on Hermes, "Unexpected token …" on V8/web) — technical noise, not a
  // message for the user.
  const raw = anyErr?.message;
  if (
    raw &&
    anyErr?.name !== 'SyntaxError' &&
    raw !== 'Request failed' && // extractError's no-detail sentinel
    !/^Request failed with status code/i.test(raw) &&
    !/^Network Error$/i.test(raw) &&
    !/^timeout of /i.test(raw) &&
    !/^JSON Parse error/i.test(raw) &&
    !/^Unexpected token/i.test(raw)
  ) {
    return clampToastMessage(raw);
  }
  return fallback;
}

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
  /**
   * Backend `error.details.code` string discriminator. The payment
   * screen reads this to detect 3-D Secure challenges
   * (`'action_required'`) returned as 402 from POST
   * /payments/create-intent. `undefined` for routes that don't set a
   * detail-code, so unrelated callers can ignore it.
   */
  detailCode?: string;
  /** Full `error.details` payload (e.g. 3DS `client_secret`,
   * `next_action`). `undefined` when the backend didn't set details. */
  details?: Record<string, unknown>;
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
    this.detailCode = extracted.detailCode;
    this.details = extracted.details;
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

// ── Phase 4 (P1-7): AsyncStorage persistence for the error ring buffer ─
// The in-memory buffer is lost on process kill / crash — exactly when we
// most need the trail. Mobile apps wire this up at startup by calling
// `initApiErrorLogPersistence(AsyncStorage)`. We DO NOT import
// `@react-native-async-storage/async-storage` here so `shared/` stays
// framework-agnostic (admin-dashboard / Node tests don't have it).
const ERROR_LOG_STORAGE_KEY = '@spinr/api-error-log';
let _persistTimer: ReturnType<typeof setTimeout> | null = null;
let _asyncStorage: {
  getItem: (k: string) => Promise<string | null>;
  setItem: (k: string, v: string) => Promise<void>;
} | null = null;

export const initApiErrorLogPersistence = async (
  storage: typeof _asyncStorage,
): Promise<void> => {
  _asyncStorage = storage;
  try {
    const stored = await storage!.getItem(ERROR_LOG_STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (Array.isArray(parsed)) _errorLog.push(...parsed.slice(-MAX_ERROR_LOG));
    }
  } catch {
    // Corrupt storage — start fresh, don't crash
  }
};

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

// Strip GPS coordinate values from URL query strings before logging or
// persisting — raw lat/lng must never appear in console output or AsyncStorage.
const _redactGpsUrl = (url: string): string =>
  url.replace(/([?&](?:lat|lng|latitude|longitude))=[^&#]*/gi, '$1=[redacted]');

const recordApiError = (entry: ApiErrorLogEntry) => {
  if (entry.surface === undefined && _defaultSurface !== undefined) {
    entry.surface = _defaultSurface;
  }
  _errorLog.push(entry);
  if (_errorLog.length > MAX_ERROR_LOG) _errorLog.shift();
  // Also console.log so it shows up in Metro / Railway mirror. Tagged so
  // it's easy to grep. Keep this concise — full data is in the buffer.
  console.log(
    `[API-ERR] ${entry.method} ${_redactGpsUrl(entry.url)} → ${entry.status} | ${entry.message}` +
    (entry.request_id ? ` | req=${entry.request_id}` : '') +
    (entry.surface ? ` | surface=${entry.surface}` : '') +
    (entry.screen ? ` | screen=${entry.screen}` : ''),
  );
  // Debounced flush to AsyncStorage so a burst of 4xx responses doesn't
  // hammer the disk. 1s window collapses bursts but still survives a
  // crash within seconds of the last error.
  //
  // PII discipline: the in-memory buffer keeps the full `data` body for live
  // on-device debugging, but the PERSISTED copy strips it — raw backend error
  // bodies can echo addresses, phone fragments, or other context that must not
  // sit at rest in AsyncStorage where a stolen/rooted device could recover it.
  // We keep only request_id/status/endpoint/redacted message for the trail.
  if (_asyncStorage) {
    if (_persistTimer) clearTimeout(_persistTimer);
    _persistTimer = setTimeout(() => {
      // Persist everything except the raw `data` body (drop it on disk).
      const redacted = _errorLog.map((e): Omit<ApiErrorLogEntry, 'data'> => ({
        ts: e.ts,
        method: e.method,
        url: _redactGpsUrl(e.url),
        status: e.status,
        message: e.message,
        request_id: e.request_id,
        exception_type: e.exception_type,
        surface: e.surface,
        screen: e.screen,
      }));
      void _asyncStorage!
        .setItem(ERROR_LOG_STORAGE_KEY, JSON.stringify(redacted))
        .catch(() => {});
    }, 1000);
  }
};

// ── SOS exemption from the 401 interceptor ───────────────────────────
// POST /rides/{id}/emergency accepts EXPIRED access tokens server-side
// (get_current_user_allow_expired) precisely so a mid-trip token lapse can
// never block an SOS. A 401 there must NOT clear the session: nuking the
// in-memory token between SOSButton retries and bouncing the user to the
// login screen mid-emergency is the worst possible failure mode. We also
// skip the silent-refresh dance — its nested /auth/refresh 401 path signs
// the user out, which is the same hazard. The error propagates to the
// SOSButton retry/failure UX instead. (/users/emergency-contacts is NOT
// exempt — that's a normal authenticated CRUD surface.)
const isSosUrl = (url: string): boolean => /^\/rides\/[^/]+\/emergency$/.test(url);

// Request paths currently inside their single allowed 503 retry (see the
// 503 branch in handleApiError). Keyed by "METHOD url" — same-path
// concurrent requests share the slot, which at worst skips a retry.
const _inflight503Retries = new Set<string>();

const handleApiError = async (response: Response, method: string, url: string, retryFn?: () => Promise<unknown>): Promise<never> => {
  // Set when this 401 went through the silent-refresh path below. Once
  // refreshTokens() has run, IT owns the logout decision (it logs out on a
  // definitive 401 and deliberately keeps the session on transient
  // network/5xx failures) — the G2 catch-all further down must not
  // second-guess it by clearing the session anyway.
  let refreshAttempted = false;
  // ── Guard: refresh endpoint itself returned 401 ──────────────────
  // If the /auth/refresh call is rejected by the server the refresh token is
  // expired or revoked. Sign the user out immediately to clear invalid state
  // rather than letting every queued request spin and eventually time-out.
  if (response.status === 401 && url.includes('/auth/refresh')) {
    // Controlled retry in progress: the refresh token the foreground sent may
    // simply have been rotated forward by the driver app's background location
    // task (both share the SecureStore `refresh_token`). The backend treats
    // replaying that just-rotated token as a benign rotation race and returns
    // 401 WITHOUT revoking the session. Let authStore.refreshTokens re-read the
    // freshest stored token and retry before any sign-out — do NOT log out here.
    //
    // Do NOT touch _refreshSubscribers here. This 401 is the NESTED
    // /auth/refresh call made from inside refreshTokens(); the OUTER refresh
    // handler below flushes subscribers via _onRefreshed once refreshTokens
    // resolves (with the new token on success, or '' on a dead session).
    // Wiping the queue here would strand every request that piggy-backed on
    // the in-flight refresh until its 15s request timeout.
    if (_suppressRefreshSignOutDepth > 0) {
      return Promise.reject(response) as Promise<never>;
    }
    console.log('[API] Refresh token rejected (401) — signing out');
    if (_signOutCallback) {
      await _signOutCallback().catch(() => {});
    } else {
      // Fallback: clear in-process state directly if the callback was not
      // registered yet (e.g. very early cold-start before authStore init).
      setInMemoryToken(null);
      try {
        const { useAuthStore } = require('../store/authStore');
        useAuthStore.getState().logout();
      } catch { /* store not yet initialised */ }
    }
    _refreshSubscribers = [];
    return Promise.reject(response) as Promise<never>;
  }


  // On 401, attempt a single silent token refresh then retry the original request.
  // SOS is exempt (see isSosUrl) — its backend route tolerates expired tokens,
  // so the refresh round-trip only adds failure modes during an emergency.
  if (response.status === 401 && _refreshCallback && retryFn && !isSosUrl(url)) {
    // Set before any await, so the fall-through to the G2 backstop below
    // always sees it — covers both the first-caller path and the
    // queued-subscriber path (a queued request whose shared refresh fails
    // rejects inside this try and would otherwise fall through to G2 and
    // get logged out).
    refreshAttempted = true;
    try {
      if (_refreshPromise) {
        // A refresh is already in-flight — queue this request and wait for
        // the new token rather than firing a second /auth/refresh call.
        // An empty token means the refresh FAILED: reject with the original
        // 401 instead of retrying. Retrying with a blank Authorization
        // header 401s again and every queued request would then start its
        // own refresh cycle — a refresh storm.
        const retried = await new Promise<unknown>((resolve, reject) => {
          _subscribeTokenRefresh((newToken) => {
            if (!newToken) {
              reject(response);
              return;
            }
            retryFn().then(resolve).catch(reject);
          });
        });
        return retried as never;
      }

      // First caller: start the refresh. Clear the dedup sentinel BEFORE
      // notifying subscribers so that any subscriber whose retryFn() triggers
      // yet another 401 sees _refreshPromise === null and starts a fresh
      // refresh cycle instead of queueing behind a completed promise.
      _refreshPromise = _refreshCallback();
      let refreshed = false;
      try {
        refreshed = await _refreshPromise;
      } finally {
        _refreshPromise = null;
      }
      if (refreshed) {
        const newToken = _inMemoryToken ?? '';
        _onRefreshed(newToken);
        return retryFn() as Promise<never>;
      }
      // Refresh returned false (transient failure) — flush subscribers with
      // empty token so they fall through to throw their own 401 errors.
      _onRefreshed('');
    } catch {
      _refreshPromise = null;
      _onRefreshed('');
    }
  }

  // On 503 (Supabase transient — see db_supabase.run_sync), retry once
  // after a short delay before surfacing the error. The backend's
  // run_sync already does its own 2-retry exponential-backoff loop, so
  // a 503 here means the burst outlasted ~2s of retries server-side.
  // Giving the rider-app one more shot after another 1.5s catches
  // longer Supabase edge hiccups without the user seeing
  // "Service temporarily unavailable: database" mid-booking.
  //
  // The _inflight503Retries guard bounds this to ONE retry: retryFn goes
  // back through client.<method>(), which passes a fresh retryFn of its
  // own, so without the guard a *persistent* 503 (e.g. Twilio unconfigured
  // → send-otp 503s every time) looped fetch → 1.5s → fetch forever. The
  // caller's promise never settled, and the user only saw an error when a
  // fetch in the loop eventually failed at the network layer — surfacing a
  // misleading "unable to reach server" instead of the server's message.
  if (response.status === 503 && retryFn && url !== '/auth/refresh' && !_inflight503Retries.has(`${method} ${url}`)) {
    const retryKey = `${method} ${url}`;
    _inflight503Retries.add(retryKey);
    try {
      await new Promise(r => setTimeout(r, 1500));
      return (await retryFn()) as never;
    } catch (retryError) {
      // The retry's own SpinrApiError carries the freshest server detail —
      // rethrow it. Anything else (network drop mid-retry) falls through so
      // the ORIGINAL 503 body below is surfaced instead of a response-less
      // error that reads as "unable to reach server".
      if (retryError instanceof SpinrApiError) throw retryError;
    } finally {
      _inflight503Retries.delete(retryKey);
    }
  }

  const errorData = await response.json().catch(() => ({}));
  const extracted = extractError(errorData, response.status);
  const message = extracted.message;
  // Phase 4 (P1-9): prefer body request_id, fall back to header. Header
  // lookups go through both `x-request-id` and `X-Trace-ID` (the OTel
  // alias the backend exposes) so plain HTTPException responses — which
  // don't carry a structured body request_id — still correlate to the
  // backend loguru line.
  const headerRequestId =
    response.headers.get('x-request-id') ||
    response.headers.get('X-Request-ID') ||
    response.headers.get('x-trace-id') ||
    response.headers.get('X-Trace-ID') ||
    undefined;
  const requestId = extracted.requestId || headerRequestId;
  const exceptionType = errorData?.error?.exception_type;
  // Ensure the structured object reflects the resolved request ID
  // (header winning over body-only callers) so downstream consumers
  // see a single source.
  if (requestId) extracted.requestId = requestId;
  // TODO(diag): wire Sentry breadcrumb here including `requestId` so
  // payment failures can be correlated mobile → backend → Stripe.
  // Sentry SDK isn't currently imported in shared/api/client.ts —
  // adding it as a dep is a separate PR.
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
  // SOS is exempt: never sign the user out mid-emergency (see isSosUrl).
  // Backstop ONLY for 401s where no silent refresh could be attempted
  // (no _refreshCallback registered yet at cold start, or no retryFn).
  // When a refresh ran, refreshTokens() owns the logout decision: it
  // logs out on definitive rejection but keeps the session on transient
  // failures — clearing here would hard-sign-out a driver mid-shift on
  // a flaky connection and force a fresh OTP login.
  if (response.status === 401 && !isSosUrl(url) && !refreshAttempted) {
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
      ...traceparentHeader(),
      ...(await appCheckHeader()),
      ...config?.headers,
    };
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetchWithTimeout(`${API_URL}/api/v1${url}`, {
      method: 'GET',
      headers,
    });

    if (!response.ok) return await handleApiError(response, 'GET', url, () => client.get(url, config));

    const data = await response.json();
    return { data, status: response.status };
  },

  async post<T = unknown>(url: string, body?: unknown, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Request-ID': generateRequestId(),
      ...deadlineHeader(),
      ...traceparentHeader(),
      ...(await appCheckHeader()),
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

    if (!response.ok) return await handleApiError(response, 'POST', url, () => client.post(url, body, config));

    const data = await response.json();
    return { data, status: response.status };
  },

  async put<T = unknown>(url: string, body?: unknown, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
    const headers: Record<string, string> = {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      'X-Request-ID': generateRequestId(),
      ...deadlineHeader(),
      ...traceparentHeader(),
      ...(await appCheckHeader()),
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

    if (!response.ok) return await handleApiError(response, 'PUT', url, () => client.put(url, body, config));

    const data = await response.json();
    return { data, status: response.status };
  },

  async patch<T = unknown>(url: string, body?: unknown, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Request-ID': generateRequestId(),
      ...deadlineHeader(),
      ...traceparentHeader(),
      ...(await appCheckHeader()),
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

    if (!response.ok) return await handleApiError(response, 'PATCH', url, () => client.patch(url, body, config));

    const data = await response.json();
    return { data, status: response.status };
  },

  async delete<T = unknown>(url: string, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      'X-Request-ID': generateRequestId(),
      ...deadlineHeader(),
      ...traceparentHeader(),
      ...(await appCheckHeader()),
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

    if (!response.ok) return await handleApiError(response, 'DELETE', url, () => client.delete(url, config));

    const data = await response.json().catch(() => ({} as T));
    return { data, status: response.status };
  },
};

export default client;
