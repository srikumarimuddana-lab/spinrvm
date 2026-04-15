/**
 * Error Reporting — unified crash/error reporting facade (SPR-03/3b).
 *
 * Implementation strategy by platform:
 *   Native (iOS/Android EAS build) → Firebase Crashlytics
 *                                     (@react-native-firebase/crashlytics, already installed)
 *   Web                             → console.error (no Crashlytics on web)
 *   Expo Go / test                  → console.error only (Crashlytics native module absent)
 *
 * Upgrade path for SPR-04:
 *   Replace Crashlytics calls with @sentry/react-native once the EAS
 *   production build pipeline is active. All call sites in ErrorBoundary,
 *   stores, and screens use this module — swapping the implementation
 *   requires changes only here.
 *
 * Interface is intentionally Sentry-compatible so the upgrade is mechanical.
 */
import { Platform } from 'react-native';

// Lazy-load the native Crashlytics module so web builds succeed and Expo Go
// doesn't crash trying to resolve a native module.
let _crashlytics: (() => any) | null = null;

function crashlytics() {
  if (_crashlytics !== null) return _crashlytics();
  if (Platform.OS === 'web') return null;
  try {
    _crashlytics = require('@react-native-firebase/crashlytics').default;
    return _crashlytics!();
  } catch {
    _crashlytics = () => null; // memoize the failure
    return null;
  }
}

/**
 * Record an exception. In production this goes to Firebase Crashlytics.
 * `context` values are attached as custom attributes before recording.
 */
export function captureException(error: Error, context?: Record<string, unknown>): void {
  try {
    const c = crashlytics();
    if (c) {
      if (context) {
        Object.entries(context).forEach(([k, v]) => {
          try { c.setAttribute(k, String(v)); } catch {}
        });
      }
      c.recordError(error);
    }
  } catch {
    // The error reporter must never crash the app.
  }
  if (__DEV__) {
    console.error('[ErrorReporting] exception:', error, context ?? '');
  }
}

/**
 * Log a non-fatal message. Appears in Crashlytics log trail.
 */
export function captureMessage(
  message: string,
  level: 'log' | 'warning' | 'error' = 'log'
): void {
  try {
    crashlytics()?.log(message);
  } catch {}
  if (__DEV__) {
    const fn =
      level === 'error' ? console.error : level === 'warning' ? console.warn : console.log;
    fn('[ErrorReporting]', message);
  }
}

/**
 * Associate subsequent events with a user identity.
 * Call after successful login; call with empty string on logout.
 */
export function setUser(userId: string, attributes?: Record<string, string>): void {
  try {
    const c = crashlytics();
    if (c) {
      c.setUserId(userId);
      if (attributes) {
        Object.entries(attributes).forEach(([k, v]) => {
          try { c.setAttribute(k, v); } catch {}
        });
      }
    }
  } catch {}
}

/**
 * Add a breadcrumb to the Crashlytics log trail. Useful for tracing the
 * steps leading up to a crash without full event logging.
 */
export function addBreadcrumb(message: string): void {
  try {
    crashlytics()?.log(message);
  } catch {}
}
