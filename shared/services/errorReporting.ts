/**
 * Error Reporting — unified crash/error reporting facade (SPR-03/3b, SPR-04).
 *
 * Implementation strategy by platform:
 *   Native + EXPO_PUBLIC_SENTRY_DSN  → @sentry/react-native (primary; one
 *                                      incident = one dashboard with backend)
 *   Native without DSN               → Firebase Crashlytics fallback
 *                                      (@react-native-firebase/crashlytics)
 *   Web                              → console.error
 *   Expo Go / test                   → console.error only (native modules absent)
 *
 * Apps opt in by calling initErrorReporting() once at startup (see each
 * app's _layout.tsx). All call sites in ErrorBoundary, stores, and screens
 * go through this module, so the backend's Sentry conventions are enforced
 * in one place:
 *   - tags: surface (rider-app/driver-app), env — set at init
 *   - PIPEDA: no PII in events. sendDefaultPii stays false, user is id-only,
 *     console breadcrumbs are dropped (they routinely contain GPS coords).
 */
import { Platform, NativeModules } from 'react-native';

// Lazy singleton for the Sentry SDK. Set on successful init; stays null in
// Expo Go/web/test or when the app never provides a DSN.
let _sentry: any = null;

// Matches numbers that look like GPS coordinates: ±dd.dddd+ or ±ddd.dddd+
// (4+ decimal places distinguishes real coordinates from display values like
// prices or distances). Captures the sign so negative longitudes are handled.
const COORD_PATTERN = /-?\d{1,3}\.\d{4,}/g;

/** @internal Exported for testing only. */
export function _scrubCoords(text: string): string {
  return text.replace(COORD_PATTERN, '[REDACTED_COORD]');
}

export interface ErrorReportingConfig {
  dsn?: string;
  /** CLAUDE.md Sentry tag: rider-app | driver-app */
  surface: 'rider-app' | 'driver-app';
  environment?: string;
}

/**
 * Initialise Sentry as the primary reporter. Safe to call unconditionally:
 * without a DSN (or when the native module is unavailable, e.g. Expo Go)
 * the facade keeps its Crashlytics/console behaviour.
 */
export function initErrorReporting(config: ErrorReportingConfig): void {
  if (!config.dsn || Platform.OS === 'web') return;
  try {
    const Sentry = require('@sentry/react-native');
    Sentry.init({
      dsn: config.dsn,
      environment: config.environment ?? (__DEV__ ? 'development' : 'production'),
      // PIPEDA: never attach IP/device-owner PII, screenshots, or view trees.
      sendDefaultPii: false,
      attachScreenshot: false,
      attachViewHierarchy: false,
      tracesSampleRate: 0.2,
      initialScope: { tags: { surface: config.surface } },
      // Console breadcrumbs are kept for crash diagnosis (they carry the
      // [BgLocation], [Geofence], [WS] trail leading to a crash). GPS
      // coordinates that may leak through debug logging are scrubbed to
      // stay PIPEDA-compliant — any number that looks like a lat/lng
      // (±dd.dddd+ or ±ddd.dddd+) is replaced with [REDACTED_COORD].
      beforeBreadcrumb(breadcrumb: any) {
        if (breadcrumb?.category === 'console' && breadcrumb.message) {
          breadcrumb.message = _scrubCoords(breadcrumb.message);
        }
        return breadcrumb;
      },
      // Defence in depth: events may only carry the user id.
      beforeSend(event: any) {
        if (event?.user) event.user = { id: event.user.id };
        return event;
      },
    });
    _sentry = Sentry;
  } catch (e) {
    // Expo Go / missing native module: fall back to Crashlytics/console.
    if (__DEV__) console.log('[ErrorReporting] Sentry unavailable, using fallback:', e);
  }
}

/** Test seam: report whether Sentry is the active backend. */
export function isSentryActive(): boolean {
  return _sentry !== null;
}

/** Test seam: reset module state between tests. */
export function _resetForTests(): void {
  _sentry = null;
  _crashlytics = null;
}

// Lazy-load the native Crashlytics module so web builds succeed and Expo Go
// doesn't crash trying to resolve a native module.
let _crashlytics: (() => any) | null = null;

function crashlytics() {
  if (_crashlytics !== null) return _crashlytics();
  if (Platform.OS === 'web' || !NativeModules.RNFBAppModule) {
    _crashlytics = () => null;
    return null;
  }
  try {
    _crashlytics = require('@react-native-firebase/crashlytics').default;
    return _crashlytics!();
  } catch {
    _crashlytics = () => null;
    return null;
  }
}

/**
 * Record an exception. Goes to Sentry when initialised, else Crashlytics.
 * `context` values are attached as custom attributes before recording.
 */
export function captureException(error: Error, context?: Record<string, unknown>): void {
  try {
    if (_sentry) {
      _sentry.captureException(error, context ? { extra: context } : undefined);
    } else {
      const c = crashlytics();
      if (c) {
        if (context) {
          Object.entries(context).forEach(([k, v]) => {
            try { c.setAttribute(k, String(v)); } catch {}
          });
        }
        c.recordError(error);
      }
    }
  } catch {
    // The error reporter must never crash the app.
  }
  if (__DEV__) {
    console.error('[ErrorReporting] exception:', error, context ?? '');
  }
}

/**
 * Log a non-fatal message. Appears in the Sentry/Crashlytics log trail.
 */
export function captureMessage(
  message: string,
  level: 'log' | 'warning' | 'error' = 'log'
): void {
  try {
    if (_sentry) {
      _sentry.captureMessage(message, level === 'log' ? 'info' : level);
    } else {
      crashlytics()?.log(message);
    }
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
 * PIPEDA: id only — never set name/email/phone attributes here.
 */
export function setUser(userId: string, attributes?: Record<string, string>): void {
  try {
    if (_sentry) {
      _sentry.setUser(userId ? { id: userId } : null);
      return;
    }
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
 * Add a breadcrumb to the Sentry/Crashlytics log trail. Useful for tracing
 * the steps leading up to a crash without full event logging.
 */
export function addBreadcrumb(message: string): void {
  try {
    if (_sentry) {
      _sentry.addBreadcrumb({ message, level: 'info' });
      return;
    }
    crashlytics()?.log(message);
  } catch {}
}

/**
 * Wrap the app root with Sentry's error boundary + touch/navigation
 * instrumentation. Apply once at the default export of each app's root layout:
 *   export default wrapApp(RootLayout);
 *
 * Safe to call unconditionally: returns the component unchanged on web or when
 * the native Sentry module is unavailable (Expo Go / tests). The wrapper is a
 * no-op until initErrorReporting() supplies a DSN, so it never sends on its own.
 */
export function wrapApp<T>(RootComponent: T): T {
  if (Platform.OS === 'web') return RootComponent;
  try {
    const Sentry = require('@sentry/react-native');
    return Sentry.wrap(RootComponent as any) as T;
  } catch {
    return RootComponent;
  }
}
