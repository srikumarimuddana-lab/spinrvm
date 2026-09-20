import { captureMessage, isSentryActive } from '../services/errorReporting';

/**
 * Extract safe, actionable diagnostics from a native App Check failure.
 *
 * Native provider errors can contain nested fields. Keep this intentionally
 * small and bounded: it is used in production logs/Crashlytics and must never
 * include an App Check token or the complete native error object.
 */
export function formatAppCheckError(error: unknown): string {
  // @react-native-firebase's NativeFirebaseError exposes the native reason as
  // `nativeErrorCode` / `nativeErrorMessage` (non-enumerable, defined in
  // app/lib/internal/NativeFirebaseError.ts). There is no `nativeError` field.
  if (error instanceof Error) {
    return JSON.stringify({
      code: getStringField(error, 'code'),
      message: truncate(error.message),
      nativeErrorCode: getStringField(error, 'nativeErrorCode'),
      nativeErrorMessage: truncate(getStringField(error, 'nativeErrorMessage')),
    });
  }

  if (typeof error === 'object' && error !== null) {
    const value = error as Record<string, unknown>;
    return JSON.stringify({
      code: toSafeString(value.code),
      message: truncate(toSafeString(value.message)),
      nativeErrorCode: toSafeString(value.nativeErrorCode),
      nativeErrorMessage: truncate(toSafeString(value.nativeErrorMessage)),
    });
  }

  return JSON.stringify({ message: truncate(String(error)) });
}

function getStringField(error: Error, field: string): string | undefined {
  return toSafeString((error as Error & Record<string, unknown>)[field]);
}

function toSafeString(value: unknown): string | undefined {
  if (typeof value === 'number') return String(value);
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}

function truncate(value: string | undefined): string | undefined {
  if (!value) return undefined;
  return value.length > 500 ? value.slice(0, 500) + '…' : value;
}

export type AppCheckFailureStage = 'init' | 'token';

const STAGE_LABEL: Record<AppCheckFailureStage, string> = {
  init: 'init failed',
  token: 'token fetch failed',
};

// One report per distinct failure per process. getAppCheckToken() runs on
// every API request, so an unregistered device would otherwise emit a Sentry
// event per request; the first occurrence carries all the signal. The key is
// stage + code + a short message prefix, so a per-attempt-varying suffix
// (retry counter, request id) cannot defeat it, and the sets are capped so a
// pathological message stream cannot grow them unboundedly.
const MAX_REPORTED_KEYS = 20;
const _reportedSentry = new Set<string>();
const _reportedNative = new Set<string>();

/** Test seam: forget which failures have already been reported. */
export function _resetAppCheckFailureReportsForTests(): void {
  _reportedSentry.clear();
  _reportedNative.clear();
}

function claim(set: Set<string>, key: string): boolean {
  if (set.has(key) || set.size >= MAX_REPORTED_KEYS) return false;
  set.add(key);
  return true;
}

/**
 * Report an App Check failure to Sentry (and optionally the native
 * Crashlytics recorder) with the bounded diagnostics from formatAppCheckError.
 *
 * Sentry drops every console breadcrumb (they routinely carry GPS), so the
 * native reason for a missing X-Firebase-AppCheck header never reached it —
 * only the downstream 401s did. The fingerprint is stage + native code so
 * each root cause is its own issue across all devices, and the code is also
 * an indexed tag. A failure seen before Sentry is initialised (headless FCM
 * launch, Android Auto cold start) is not marked as sent, so it is reported
 * if Sentry comes up later in the same process. Never throws.
 */
export function reportAppCheckFailure(
  stage: AppCheckFailureStage,
  error: unknown,
  recordNative?: (error: Error) => void,
): void {
  try {
    const diagnostics = formatAppCheckError(error);
    let code = 'unknown';
    let messagePrefix = '';
    try {
      const parsed = JSON.parse(diagnostics) as { code?: string; message?: string };
      code = parsed.code ?? 'unknown';
      messagePrefix = (parsed.message ?? '').slice(0, 120);
    } catch {}
    const key = `${stage}|${code}|${messagePrefix}`;
    const message = `[AppCheck] ${STAGE_LABEL[stage]}: ${diagnostics}`;

    if (recordNative && claim(_reportedNative, key)) {
      try {
        recordNative(new Error(message));
      } catch {}
    }

    if (isSentryActive() && claim(_reportedSentry, key)) {
      captureMessage(message, 'error', {
        fingerprint: ['appcheck-failure', stage, code],
        tags: { domain: 'auth', appcheck_stage: stage, appcheck_code: code },
      });
    }
  } catch {
    // Diagnostics must never take the app down with them.
  }
}
