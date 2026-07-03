import { NativeModules, Platform } from 'react-native';

/**
 * Firebase Services — FCM, Crashlytics, App Check
 *
 * Uses the @react-native-firebase v22+ **modular** API (getMessaging(),
 * getCrashlytics(), initializeAppCheck(), getToken(), …) rather than the
 * deprecated namespaced API (messaging(), crashlytics(), appCheck()). The
 * namespaced API still works on v24 but logs a deprecation warning on every
 * call and is slated for removal in the next major release.
 *
 * These are native modules — only work in custom dev/release builds, NOT Expo Go.
 */

// Gate on native-module presence. require()-ing @react-native-firebase
// side-effects construct a NativeEventEmitter for RNFBAppModule; in Expo Go
// that throws synchronously from module scope, and LogBox still reports it
// even when wrapped in try/catch. Skip the require entirely if the native
// module isn't linked.
const hasFirebaseNative = !!NativeModules.RNFBAppModule;

// Type-only imports — runtime loads via require() guarded by hasFirebaseNative.
// `typeof import(...)` gives the full module namespace type (including the
// modular function exports) without pulling the packages into shared/'s bundle.
type MessagingModule = typeof import('@react-native-firebase/messaging');
type CrashlyticsModule = typeof import('@react-native-firebase/crashlytics');
type AppCheckModule = typeof import('@react-native-firebase/app-check');
type AppCheckInstance = import('@react-native-firebase/app-check').AppCheck;
type RemoteMessage = import('@react-native-firebase/messaging').FirebaseMessagingTypes.RemoteMessage;

// The whole module namespaces are loaded (not `.default`) so the modular
// named exports (getMessaging, getToken, initializeAppCheck, …) are available.
let messagingApi: MessagingModule | null = null;
let crashlyticsApi: CrashlyticsModule | null = null;
let appCheckApi: AppCheckModule | null = null;

if (hasFirebaseNative) {
  try {
    messagingApi = require('@react-native-firebase/messaging');
  } catch (e) { console.log('[Firebase] messaging load error:', e); }

  try {
    crashlyticsApi = require('@react-native-firebase/crashlytics');
  } catch (e) { console.log('[Firebase] crashlytics load error:', e); }

  try {
    appCheckApi = require('@react-native-firebase/app-check');
  } catch (e) { console.log('[Firebase] app-check load error:', e); }
} else {
  console.log('[Firebase] native module not linked (Expo Go) — push/crashlytics disabled');
}


/**
 * Initialize Firebase services. Call once on app startup.
 *
 * Idempotent — the work runs at most once and every caller awaits the same
 * promise. This matters because it's now invoked from two entry points: the
 * mounted root layout (app/_layout cold start) AND the headless FCM/Notifee
 * background task (services/backgroundMessaging), which never mounts the
 * layout. Without init here, App Check has no provider in the headless context
 * and getAppCheckToken() returns null.
 */
let _initServicesPromise: Promise<void> | null = null;

// The modular App Check getToken(appCheckInstance, …) needs the instance
// returned by initializeAppCheck(). We cache it here from _doInit so
// getAppCheckToken() (called per-request + per background batch) can reuse it.
let _appCheckInstance: AppCheckInstance | null = null;

export function initFirebaseServices(): Promise<void> {
  if (_initServicesPromise) return _initServicesPromise;
  _initServicesPromise = _doInitFirebaseServices();
  return _initServicesPromise;
}

async function _doInitFirebaseServices(): Promise<void> {
  // 1. Crashlytics — enable automatic crash reporting
  if (crashlyticsApi) {
    try {
      const crashlytics = crashlyticsApi.getCrashlytics();
      await crashlyticsApi.setCrashlyticsCollectionEnabled(crashlytics, true);
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
  //
  // newReactNativeFirebaseAppCheckProvider() exists only on the namespaced
  // module instance (it has no modular replacement and so does not warn);
  // everything after it — initializeAppCheck()/getToken() — is modular.
  if (appCheckApi) {
    try {
      const provider = appCheckApi.default().newReactNativeFirebaseAppCheckProvider();
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

      // First arg (app) omitted — initializeAppCheck() falls back to the
      // default app internally, so we don't need @react-native-firebase/app.
      _appCheckInstance = await appCheckApi.initializeAppCheck(undefined, {
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
  if (!messagingApi) return false;
  try {
    const messaging = messagingApi.getMessaging();
    const authStatus = await messagingApi.requestPermission(messaging);
    const enabled =
      authStatus === messagingApi.AuthorizationStatus.AUTHORIZED ||
      authStatus === messagingApi.AuthorizationStatus.PROVISIONAL;
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
  if (!messagingApi) return null;

  try {
    const messaging = messagingApi.getMessaging();
    // Request permission (iOS — Android auto-grants)
    const authStatus = await messagingApi.requestPermission(messaging);
    const enabled =
      authStatus === messagingApi.AuthorizationStatus.AUTHORIZED ||
      authStatus === messagingApi.AuthorizationStatus.PROVISIONAL;

    if (!enabled) {
      console.log('[Firebase] Push permission denied');
      return null;
    }

    // Get FCM token
    const token = await messagingApi.getToken(messaging);
    // A token prefix is still a device-routable identifier — keep it out of
    // production logs. In dev we log only whether one was obtained.
    if (__DEV__) console.log('[Firebase] FCM token obtained:', token ? 'yes' : 'no');
    return token;
  } catch (e) {
    console.log('[Firebase] FCM token error:', e);
    return null;
  }
}


/**
 * Register a handler for incoming push notifications (foreground).
 */
export function onForegroundMessage(handler: (message: RemoteMessage) => void) {
  if (!messagingApi) return () => {};
  return messagingApi.onMessage(messagingApi.getMessaging(), handler);
}


/**
 * Set the background message handler.
 * Must be called at the TOP LEVEL (outside of any component).
 */
export function setBackgroundMessageHandler(handler: (message: RemoteMessage) => Promise<unknown> | void) {
  if (!messagingApi) return;
  // Called at module scope from app/_layout.tsx — this runs during initial
  // bundle evaluation, BEFORE Sentry init and any error boundary. An unguarded
  // throw here (e.g. getMessaging() when the native [DEFAULT] Firebase app
  // failed to configure) aborts the entire app at launch in release builds.
  // Degrade to no-background-push and log loudly instead.
  try {
    messagingApi.setBackgroundMessageHandler(messagingApi.getMessaging(), (msg) => handler(msg) ?? Promise.resolve());
  } catch (e) {
    console.error('[Firebase] setBackgroundMessageHandler failed — background push disabled:', e);
  }
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
  if (!messagingApi) return () => {};
  return messagingApi.onTokenRefresh(messagingApi.getMessaging(), onRefresh);
}


/**
 * Log a custom event to Crashlytics.
 */
export function logCrashlyticsEvent(message: string) {
  if (!crashlyticsApi) return;
  crashlyticsApi.log(crashlyticsApi.getCrashlytics(), message);
}


/**
 * Set user ID for Crashlytics (helps identify crashes per user).
 */
export function setCrashlyticsUser(userId: string) {
  if (!crashlyticsApi) return;
  crashlyticsApi.setUserId(crashlyticsApi.getCrashlytics(), userId);
}


/**
 * Record a non-fatal error in Crashlytics.
 */
export function recordError(error: Error) {
  if (!crashlyticsApi) return;
  crashlyticsApi.recordError(crashlyticsApi.getCrashlytics(), error);
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
  if (!appCheckApi) return null;
  try {
    // The modular getToken() needs the AppCheck instance from init. If init
    // hasn't run in this context yet (e.g. a headless task), run it now —
    // initFirebaseServices() is idempotent and cheap when already resolved.
    if (!_appCheckInstance) await initFirebaseServices();
    if (!_appCheckInstance) return null;
    const result = await appCheckApi.getToken(_appCheckInstance, false);
    return result?.token ?? null;
  } catch (e) {
    console.log('[Firebase] App Check token fetch error:', e);
    return null;
  }
}
