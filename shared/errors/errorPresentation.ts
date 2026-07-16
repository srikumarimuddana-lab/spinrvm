/**
 * Error → toast presentation: the single place that decides a toast's TITLE
 * and SEVERITY from the backend's structured error contract.
 *
 * Why this exists: previously every catch site independently chose a title
 * (often a generic screen-level string, or a regex sniff of the human message
 * like `/email/i.test(msg)`). That decentralised decision is what let toasts
 * drift out of sync with the actual failure. This module keys off the
 * backend's numeric `error.code` (mirror of backend ErrorCode) and, for
 * "already exists" style failures, `error.details.field` — so a duplicate
 * email yields "Email Already In Use" from DATA, not string-matching.
 *
 * Pure and dependency-free (no React Native, no API client) so it stays
 * trivially unit-testable and can't drag the fetch/SecureStore client into a
 * toast module's graph. The display MESSAGE is resolved separately by
 * getApiErrorMessage (which handles 429/clamping); each app's notifyError glue
 * composes the two.
 */
import { clampToastMessage, TOAST_TITLE_MAX } from '../utils/toastMessage';

export type ToastSeverity = 'success' | 'info' | 'warning' | 'error';

// Numeric ErrorCode values — mirror of backend/utils/error_handling.py.
// Only codes we map to a specific presentation are listed; keep in sync when
// the backend adds a user-facing code worth a distinct title.
const CODE = {
  AUTH_INVALID_CREDENTIALS: 1004,
  AUTH_OTP_EXPIRED: 1007,
  AUTH_OTP_INVALID: 1008,
  VALIDATION_ERROR: 2001,
  VALIDATION_INVALID_FORMAT: 2002,
  VALIDATION_MISSING_FIELD: 2003,
  RESOURCE_ALREADY_EXISTS: 3002,
  RESOURCE_CONFLICT: 3003,
  RIDE_ALREADY_CANCELLED: 4003,
  RIDE_NO_DRIVERS_AVAILABLE: 4004,
  RIDE_PRICE_MISMATCH: 4005,
  DRIVER_DOCUMENTS_PENDING: 5004,
  DRIVER_DOCUMENTS_REJECTED: 5005,
  DRIVER_QUOTA_EXCEEDED: 5006,
  PAYMENT_FAILED: 6001,
  PAYMENT_METHOD_INVALID: 6002,
  PAYMENT_INSUFFICIENT_FUNDS: 6003,
  PAYMENT_UNPAID_RIDE_BLOCK: 6005,
  SERVICE_UNAVAILABLE: 9002,
  RATE_LIMIT_EXCEEDED: 9003,
  DUPLICATE_RECORD: 9006,
} as const;

interface Presentation {
  title: string;
  severity: ToastSeverity;
}

// code → {title, severity}. Used when the code implies a clearer or more
// consistent title than the caller's screen-level fallback.
const CODE_PRESENTATION: Record<number, Presentation> = {
  [CODE.AUTH_INVALID_CREDENTIALS]: { title: 'Sign-in Failed', severity: 'error' },
  [CODE.AUTH_OTP_INVALID]: { title: 'Incorrect Code', severity: 'error' },
  [CODE.AUTH_OTP_EXPIRED]: { title: 'Code Expired', severity: 'warning' },
  [CODE.VALIDATION_ERROR]: { title: 'Check Your Details', severity: 'warning' },
  [CODE.VALIDATION_INVALID_FORMAT]: { title: 'Check Your Details', severity: 'warning' },
  [CODE.VALIDATION_MISSING_FIELD]: { title: 'Missing Information', severity: 'warning' },
  [CODE.RESOURCE_CONFLICT]: { title: 'Already Updated', severity: 'warning' },
  [CODE.RIDE_ALREADY_CANCELLED]: { title: 'Ride Already Cancelled', severity: 'warning' },
  [CODE.RIDE_NO_DRIVERS_AVAILABLE]: { title: 'No Drivers Available', severity: 'warning' },
  [CODE.RIDE_PRICE_MISMATCH]: { title: 'Price Updated', severity: 'warning' },
  [CODE.DRIVER_DOCUMENTS_PENDING]: { title: 'Documents Under Review', severity: 'warning' },
  [CODE.DRIVER_DOCUMENTS_REJECTED]: { title: 'Documents Rejected', severity: 'error' },
  [CODE.DRIVER_QUOTA_EXCEEDED]: { title: 'Daily Limit Reached', severity: 'info' },
  [CODE.PAYMENT_FAILED]: { title: 'Payment Failed', severity: 'error' },
  [CODE.PAYMENT_METHOD_INVALID]: { title: 'Payment Method Issue', severity: 'error' },
  [CODE.PAYMENT_INSUFFICIENT_FUNDS]: { title: 'Insufficient Balance', severity: 'error' },
  [CODE.PAYMENT_UNPAID_RIDE_BLOCK]: { title: 'Unpaid Ride', severity: 'error' },
  [CODE.SERVICE_UNAVAILABLE]: { title: 'Temporarily Unavailable', severity: 'warning' },
  [CODE.RATE_LIMIT_EXCEEDED]: { title: 'Too Many Attempts', severity: 'warning' },
};

// Field-specific titles for "already exists" failures, keyed by
// `error.details.field`. This is what turns a duplicate-email 400 into a toast
// the user can act on without reading the body.
const FIELD_ALREADY_IN_USE: Record<string, string> = {
  email: 'Email Already In Use',
  phone: 'Phone Already In Use',
};

const ALREADY_EXISTS_CODES = new Set<number>([
  CODE.RESOURCE_ALREADY_EXISTS,
  CODE.DUPLICATE_RECORD,
]);

/**
 * Numeric ErrorCode carried by an error, or 0 when unknown. Duck-typed across
 * the shapes the API client throws: SpinrApiError (`.code`), a raw
 * Axios-style error (`.response.data.error.code`), and RateLimitError
 * (identified by name — it carries no numeric code).
 */
export function getErrorCode(err: unknown): number {
  if (!err || typeof err !== 'object') return 0;
  const e = err as {
    name?: unknown;
    code?: unknown;
    response?: { data?: { error?: { code?: unknown } } };
  };
  if (e.name === 'RateLimitError') return CODE.RATE_LIMIT_EXCEEDED;
  if (typeof e.code === 'number' && e.code > 0) return e.code;
  const bodyCode = e.response?.data?.error?.code;
  return typeof bodyCode === 'number' ? bodyCode : 0;
}

/**
 * The offending field name for a field-scoped error (e.g. `'email'`), read
 * from `error.details.field`. Present on SpinrApiError (`.details`) and on the
 * raw response body (`.response.data.error.details.field`).
 */
export function getErrorField(err: unknown): string | undefined {
  if (!err || typeof err !== 'object') return undefined;
  const e = err as {
    details?: { field?: unknown };
    response?: { data?: { error?: { details?: { field?: unknown } } } };
  };
  const field = e.details?.field ?? e.response?.data?.error?.details?.field;
  return typeof field === 'string' && field ? field : undefined;
}

export interface ErrorPresentation {
  /** Toast title, already clamped to TOAST_TITLE_MAX. */
  title: string;
  severity: ToastSeverity;
  /** Resolved numeric code (0 if unknown) — handy for callers that branch. */
  code: number;
}

/**
 * Decide a toast's title + severity for a caught error.
 *
 * Resolution order:
 *   1. Field-scoped "already in use" (duplicate email/phone) → field-specific title
 *   2. A code with a mapped presentation → that title + severity
 *   3. Otherwise → the caller's screen-level `fallbackTitle` at `error` severity
 *
 * Never invents a message — the display body is resolved by getApiErrorMessage
 * in each app's notifyError glue.
 */
export function presentError(err: unknown, opts: { fallbackTitle: string }): ErrorPresentation {
  const code = getErrorCode(err);
  let title = opts.fallbackTitle;
  let severity: ToastSeverity = 'error';

  if (ALREADY_EXISTS_CODES.has(code)) {
    const field = getErrorField(err);
    title = (field && FIELD_ALREADY_IN_USE[field]) || 'Already Registered';
  } else {
    const mapped = CODE_PRESENTATION[code];
    if (mapped) {
      title = mapped.title;
      severity = mapped.severity;
    }
  }

  return { title: clampToastMessage(title, TOAST_TITLE_MAX), severity, code };
}
