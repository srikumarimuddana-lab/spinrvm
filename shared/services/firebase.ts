import { NativeModules, Platform } from 'react-native';

/**
 * Firebase Services — FCM, Crashlytics, App Check
 *
 * These use @react-native-firebase (native modules).
 * Only work in custom dev builds, NOT Expo Go.
 */

// Gate on native-module presence. require()-ing @react-native-firebase
// side-effects construct a NativeEventEmitter for RNFBAppModule; in Expo Go
// that throws synchronously from module scope, and LogBox still reports it
// even when wrapped in try/catch. Skip the require entirely if the native
// module isn't linked.
const hasFirebaseNative = !!NativeModules.RNFBAppModule;

// Type-only imports — runtime loads via require() guarded by hasFirebaseNative.
// `typeof import(...)` gives us the correct factory-function type without
// pulling the packages into shared/'s runtime bundle.
type MessagingFactory = typeof import('@react-native-firebase/messaging').default;
type CrashlyticsFactory = typeof import('@react-native-firebase/crashlytics').default;
type AppCheckFactory = typeof import('@react-native-firebase/app-check').default;

let messaging: MessagingFactory | null = null;
let crashlytics: CrashlyticsFactory | null = null;
let appCheck: AppCheckFactory | null = null;

if (hasFirebaseNative) {
  try {
    messaging = require('@react-native-firebase/messaging').default;
  } catch (e) { console.log('[Firebase] messaging load error:', e); }

  try {
    crashlytics = require('@react-native-firebase/crashlytics').default;
  } catch (e) { console.log('[Firebase] crashlytics load error:', e); }

  try {
    appCheck = require('@react-native-firebase/app-check').default;
  } catch (e) { console.log('[Firebase] app-check load error:', e); }
} else {
  console.log('[Firebase] native module not linked (Expo Go) — push/crashlytics disabled');
}


/**
 * Initialize Firebase services. Call once on app startup.
 */
export async function initFirebaseServices() {
  // 1. Crashlytics — enable automatic crash reporting
  if (crashlytics) {
    try {
      await crashlytics().setCrashlyticsCollectionEnabled(true);
      console.log('[Firebase] Crashlytics enabled');
    } catch (e) {
      console.log('[Firebase] Crashlytics init error:', e);
    }
  }

  // 2. App Check — verify requests come from real app
  // The provider factory ('playIntegrity' / 'deviceCheck') is set natively
  // via the @react-native-firebase/app-check config plugin in app.config.ts.
  // Here we configure the runtime provider to match. Without configure(),
  // initializeAppCheck() throws "no provider or no provider options defined".
  if (appCheck) {
    try {
      const provider = appCheck().newReactNativeFirebaseAppCheckProvider();
      provider.configure({
        android: {
          provider: 'playIntegrity',
        },
        apple: {
          provider: 'deviceCheck',
        },
        web: {
          // Required by the type signature; not used on native.
          provider: 'reCaptchaV3',
          siteKey: 'unused',
        },
      });

      await appCheck().initializeAppCheck({
        provider,
        isTokenAutoRefreshEnabled: true,
      });
      console.log('[Firebase] App Check initialized');
    } catch (e) {
      console.log('[Firebase] App Check init error:', e);
    }
  }
}


/**
 * Request notification permission only (no token fetch).
 * Call on first open so the OS dialog appears before login.
 */
export async function requestNotificationPermission(): Promise<boolean> {
  if (!messaging) return false;
  try {
    const authStatus = await messaging().requestPermission();
    const enabled =
      authStatus === messaging.AuthorizationStatus.AUTHORIZED ||
      authStatus === messaging.AuthorizationStatus.PROVISIONAL;
    console.log('[Firebase] Notification permission:', enabled ? 'granted' : 'denied');
    return enabled;
  } catch (e) {
    console.log('[Firebase] Permission request failed:', e);
    return false;
  }
}


/**
 * Request push notification permission and get FCM token.
 * Returns the token string or null.
 */
export async function requestPushPermissionAndGetToken(): Promise<string | null> {
  if (!messaging) return null;

  try {
    // Request permission (iOS — Android auto-grants)
    const authStatus = await messaging().requestPermission();
    const enabled =
      authStatus === messaging.AuthorizationStatus.AUTHORIZED ||
      authStatus === messaging.AuthorizationStatus.PROVISIONAL;

    if (!enabled) {
      console.log('[Firebase] Push permission denied');
      return null;
    }

    // Get FCM token
    const token = await messaging().getToken();
    console.log('[Firebase] FCM Token:', token?.substring(0, 20) + '...');
    return token;
  } catch (e) {
    console.log('[Firebase] FCM token error:', e);
    return null;
  }
}


/**
 * Register a handler for incoming push notifications (foreground).
 */
export function onForegroundMessage(handler: (message: import('@react-native-firebase/messaging').FirebaseMessagingTypes.RemoteMessage) => void) {
  if (!messaging) return () => {};
  return messaging().onMessage(handler);
}


/**
 * Set the background message handler.
 * Must be called at the TOP LEVEL (outside of any component).
 */
export function setBackgroundMessageHandler(handler: (message: import('@react-native-firebase/messaging').FirebaseMessagingTypes.RemoteMessage) => Promise<unknown> | void) {
  if (!messaging) return;
  messaging().setBackgroundMessageHandler((msg) => handler(msg) ?? Promise.resolve());
}


/**
 * Subscribe to FCM token refresh events.
 *
 * Firebase periodically rotates the device token. If the app doesn't
 * re-register the new token with the backend, push delivery fails
 * silently until the next cold start (which calls
 * `requestPushPermissionAndGetToken` → registers the new token).
 *
 * Call this once from the root layout effect. The returned unsubscribe
 * function should be called on unmount.
 *
 * @param onRefresh - called with the new FCM token string.
 */
export function onTokenRefresh(onRefresh: (newToken: string) => void): () => void {
  if (!messaging) return () => {};
  return messaging().onTokenRefresh(onRefresh);
}


/**
 * Log a custom event to Crashlytics.
 */
export function logCrashlyticsEvent(message: string) {
  if (!crashlytics) return;
  crashlytics().log(message);
}


/**
 * Set user ID for Crashlytics (helps identify crashes per user).
 */
export function setCrashlyticsUser(userId: string) {
  if (!crashlytics) return;
  crashlytics().setUserId(userId);
}


/**
 * Record a non-fatal error in Crashlytics.
 */
export function recordError(error: Error) {
  if (!crashlytics) return;
  crashlytics().recordError(error);
}


/**
 * Fetch a fresh Firebase App Check token.
 *
 * Returns null when App Check is not initialized (Expo Go, unit tests, devices
 * without Play Integrity / DeviceCheck). The API client treats a null as
 * "omit the header" rather than blocking the request, so the backend's
 * enforcement mode determines whether the call succeeds.
 */
export async function getAppCheckToken(): Promise<string | null> {
  if (!appCheck) return null;
  try {
    const result = await appCheck().getToken(false);
    return result?.token ?? null;
  } catch (e) {
    console.log('[Firebase] App Check token fetch error:', e);
    return null;
  }
}
