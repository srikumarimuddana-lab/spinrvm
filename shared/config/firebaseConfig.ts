import { initializeApp, getApps } from 'firebase/app';
import { getAuth, Auth } from 'firebase/auth';

/**
 * Firebase JS SDK configuration for Web + Expo Go fallbacks.
 *
 * Note: Firebase web-SDK config values are NOT secrets — per Firebase
 * docs they're bundled into every client and security comes from
 * Firebase Security Rules, not from hiding the config. We still pull
 * them from env vars instead of hardcoding them so:
 *
 *   (a) a cloned repo doesn't accidentally write to somebody else's
 *       Firebase project, and
 *   (b) dev/staging/prod can swap projects without code changes.
 *
 * On native platforms the real Firebase config lives in
 * `google-services.json` (Android) and `GoogleService-Info.plist`
 * (iOS), which `@react-native-firebase` reads directly — so this
 * module is primarily a compatibility shim for the Expo Web build
 * and legacy call sites that import from the JS SDK.
 */
const firebaseConfig = {
  apiKey: process.env.EXPO_PUBLIC_FIREBASE_API_KEY || "",
  authDomain: process.env.EXPO_PUBLIC_FIREBASE_AUTH_DOMAIN || "",
  projectId: process.env.EXPO_PUBLIC_FIREBASE_PROJECT_ID || "",
  storageBucket: process.env.EXPO_PUBLIC_FIREBASE_STORAGE_BUCKET || "",
  messagingSenderId: process.env.EXPO_PUBLIC_FIREBASE_MESSAGING_SENDER_ID || "",
  appId: process.env.EXPO_PUBLIC_FIREBASE_APP_ID || "",
  measurementId: process.env.EXPO_PUBLIC_FIREBASE_MEASUREMENT_ID || "",
};

// Initialize Firebase (avoid duplicate init on hot reload)
let app;
let auth: Auth;

if (!firebaseConfig.apiKey || !firebaseConfig.projectId) {
  // No credentials configured — skip Firebase entirely. The app falls back
  // to backend OTP auth. Native builds use google-services.json /
  // GoogleService-Info.plist which are read by @react-native-firebase
  // independently of this module.
  auth = {} as Auth;
} else {
  try {
    // Only initialize if not already done (hot reload safety)
    const existingApps = getApps();
    app = existingApps.length === 0
      ? initializeApp(firebaseConfig)
      : existingApps.find(a => a.name === '[DEFAULT]') || existingApps[0];
    auth = getAuth(app);
  } catch (error: any) {
    console.warn('[Firebase] init error:', error.message);
    auth = {} as Auth;
  }
}

export { app, auth, firebaseConfig };
