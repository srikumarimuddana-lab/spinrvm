import { NativeModules } from 'react-native';

/**
 * Report a non-fatal error to Firebase Crashlytics.
 * Call this in catch blocks that recover gracefully so the error is
 * visible in the Crashlytics dashboard without crashing the app.
 *
 * In Expo Go the @react-native-firebase native module isn't linked, so
 * we detect that and no-op instead of throwing at module load. Same gating
 * pattern as shared/services/firebase.ts — checking NativeModules before
 * require() avoids the synchronous NativeEventEmitter crash triggered by
 * side effects in the firebase/crashlytics module scope.
 */
const hasFirebaseNative = !!NativeModules.RNFBAppModule;
// Whole module namespace (not `.default`) so the v22+ modular named exports
// (getCrashlytics, setAttribute, recordError) are available — the namespaced
// crashlytics() API is deprecated and logs a warning on every call.
let crashlyticsApi: typeof import('@react-native-firebase/crashlytics') | null = null;
if (hasFirebaseNative) {
  try {
    // Guarded native-module require — must stay runtime require(), not a
    // static import, so it only executes once NativeModules.RNFBAppModule
    // is confirmed present (see file header: a static import's module-scope
    // side effects would crash before this check ever runs in Expo Go).
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    crashlyticsApi = require('@react-native-firebase/crashlytics');
  } catch (e) {
    console.log('[Crashlytics] module load error:', e);
  }
}

export function recordNonFatal(
  error: unknown,
  context?: Record<string, string>
): void {
  if (!crashlyticsApi) return;
  try {
    const crashlytics = crashlyticsApi.getCrashlytics();
    const err = error instanceof Error ? error : new Error(String(error));
    if (context) {
      for (const [key, value] of Object.entries(context)) {
        crashlyticsApi.setAttribute(crashlytics, key, value);
      }
    }
    crashlyticsApi.recordError(crashlytics, err);
  } catch {
    // Never let Crashlytics reporting itself throw — it would shadow the
    // original error and confuse callers.
  }
}
