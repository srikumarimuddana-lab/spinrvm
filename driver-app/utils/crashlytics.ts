import { NativeModules } from 'react-native';
import { captureException } from '@shared/services/errorReporting';

/**
 * Report a non-fatal error to crash analytics.
 *
 * Routes through the shared errorReporting facade (Sentry primary, Crashlytics
 * fallback) so every call site that uses recordNonFatal appears in the Sentry
 * dashboard. Also records to Crashlytics directly as belt-and-suspenders — the
 * shared facade already does this when Sentry is unavailable, but the direct
 * call ensures Crashlytics always gets a copy regardless of Sentry init state.
 *
 * In Expo Go the @react-native-firebase native module isn't linked, so
 * we detect that and skip the direct Crashlytics path. Same gating
 * pattern as shared/services/firebase.ts — checking NativeModules before
 * require() avoids the synchronous NativeEventEmitter crash triggered by
 * side effects in the firebase/crashlytics module scope.
 */
const hasFirebaseNative = !!NativeModules.RNFBAppModule;
let crashlyticsApi: typeof import('@react-native-firebase/crashlytics') | null = null;
if (hasFirebaseNative) {
  try {
    crashlyticsApi = require('@react-native-firebase/crashlytics');
  } catch (e) {
    console.log('[Crashlytics] module load error:', e);
  }
}

export function recordNonFatal(
  error: unknown,
  context?: Record<string, string>
): void {
  const err = error instanceof Error ? error : new Error(String(error));

  // Primary: route through the shared facade so the error reaches Sentry
  // (when initialised) and appears alongside backend events in one dashboard.
  try {
    captureException(err, context);
  } catch {
    // Never let reporting crash the app.
  }

  // Secondary: direct Crashlytics write. The shared facade already falls back
  // to Crashlytics when Sentry is unavailable, but the direct call means
  // Crashlytics always gets a copy even if the facade's Sentry path throws.
  if (!crashlyticsApi) return;
  try {
    const crashlytics = crashlyticsApi.getCrashlytics();
    if (context) {
      for (const [key, value] of Object.entries(context)) {
        crashlyticsApi.setAttribute(crashlytics, key, value);
      }
    }
    crashlyticsApi.recordError(crashlytics, err);
  } catch {
    // Never let Crashlytics reporting itself throw.
  }
}
