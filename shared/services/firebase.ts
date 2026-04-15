import { Platform } from 'react-native';

/**
 * Firebase Services — FCM, Crashlytics, App Check
 *
 * These use @react-native-firebase (native modules).
 * Only work in custom dev builds, NOT Expo Go.
 */

// Safe imports — these will fail in Expo Go, so we wrap them
let messaging: any = null;
let crashlytics: any = null;
let appCheck: any = null;

try {
  messaging = require('@react-native-firebase/messaging').default;
} catch { console.log('[Firebase] messaging not available (Expo Go?)'); }

try {
  crashlytics = require('@react-native-firebase/crashlytics').default;
} catch { console.log('[Firebase] crashlytics not available'); }

try {
  appCheck = require('@react-native-firebase/app-check').default;
} catch { console.log('[Firebase] app-check not available'); }


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
  if (appCheck) {
    try {
      const provider = Platform.OS === 'android'
        ? appCheck.newReactNativeFirebaseAppCheckProvider()
        : appCheck.newReactNativeFirebaseAppCheckProvider();

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
export function onForegroundMessage(handler: (message: any) => void) {
  if (!messaging) return () => {};
  return messaging().onMessage(handler);
}


/**
 * Set the background message handler.
 * Must be called at the TOP LEVEL (outside of any component).
 */
export function setBackgroundMessageHandler(handler: (message: any) => void) {
  if (!messaging) return;
  messaging().setBackgroundMessageHandler(handler);
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
