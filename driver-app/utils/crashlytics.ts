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
let crashlytics: any = null;
if (hasFirebaseNative) {
  try {
    crashlytics = require('@react-native-firebase/crashlytics').default;
  } catch (e) {
    console.log('[Crashlytics] module load error:', e);
  }
}

export function recordNonFatal(
  error: unknown,
  context?: Record<string, string>
): void {
  if (!crashlytics) return;
  try {
    const err = error instanceof Error ? error : new Error(String(error));
    if (context) {
      for (const [key, value] of Object.entries(context)) {
        crashlytics().setAttribute(key, value);
      }
    }
    crashlytics().recordError(err);
  } catch {
    // Never let Crashlytics reporting itself throw — it would shadow the
    // original error and confuse callers.
  }
}
