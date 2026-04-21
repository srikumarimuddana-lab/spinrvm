import crashlytics from '@react-native-firebase/crashlytics';

/**
 * Report a non-fatal error to Firebase Crashlytics.
 * Call this in catch blocks that recover gracefully so the error is
 * visible in the Crashlytics dashboard without crashing the app.
 *
 * @param error - The caught error (any type — coerced to Error if needed)
 * @param context - Optional key/value attributes added as custom keys
 */
export function recordNonFatal(
  error: unknown,
  context?: Record<string, string>
): void {
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
