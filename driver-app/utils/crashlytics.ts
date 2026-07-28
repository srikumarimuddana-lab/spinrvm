import { captureException } from '@shared/services/errorReporting';

/**
 * Report a non-fatal error to crash analytics.
 *
 * Thin delegate to the shared errorReporting facade, which routes to Sentry
 * when a DSN is configured and falls back to Firebase Crashlytics otherwise.
 * Keeping the driver app's own Crashlytics write here as well would double-
 * record every non-fatal on the (likely) no-DSN path: both gate on
 * `NativeModules.RNFBAppModule`, and the modular `getCrashlytics()` handle and
 * the facade's namespaced one address the same native singleton.
 *
 * This wrapper is kept rather than inlined so the ~14 existing call sites stay
 * unchanged and the Expo-Go/native gating lives in exactly one place.
 */
export function recordNonFatal(
  error: unknown,
  context?: Record<string, string>
): void {
  // captureException swallows its own failures by contract, so no try/catch here.
  captureException(
    error instanceof Error ? error : new Error(String(error)),
    context,
  );
}
