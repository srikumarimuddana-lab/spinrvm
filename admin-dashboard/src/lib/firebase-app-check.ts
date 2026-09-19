import { initializeApp, getApps, type FirebaseApp } from "firebase/app";
import {
  initializeAppCheck,
  getToken,
  ReCaptchaV3Provider,
  type AppCheck,
} from "firebase/app-check";

// Public, client-embedded values by design — same category as
// NEXT_PUBLIC_GOOGLE_MAPS_API_KEY / NEXT_PUBLIC_PROTOMAPS_API_KEY in
// .env.example. Never put a Firebase *service account* or backend secret here.
const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
};

let appCheckInstance: AppCheck | null = null;
let appCheckInitAttempted = false;

function getFirebaseApp(): FirebaseApp | null {
  if (!firebaseConfig.apiKey || !firebaseConfig.projectId || !firebaseConfig.appId) {
    return null;
  }
  return getApps().length ? getApps()[0]! : initializeApp(firebaseConfig);
}

function getAppCheckInstance(): AppCheck | null {
  if (appCheckInstance) return appCheckInstance;
  if (appCheckInitAttempted) return null; // don't retry init every call after a failure
  appCheckInitAttempted = true;

  const siteKey = process.env.NEXT_PUBLIC_RECAPTCHA_SITE_KEY;
  const app = getFirebaseApp();
  if (!app || !siteKey) return null;

  try {
    appCheckInstance = initializeAppCheck(app, {
      provider: new ReCaptchaV3Provider(siteKey),
      isTokenAutoRefreshEnabled: true,
    });
    return appCheckInstance;
  } catch {
    return null;
  }
}

// getToken() talks to Google's reCAPTCHA servers; a stalled request must not
// hang the upload flow forever. On timeout we fail open, same as any other
// getToken() failure — the backend enforcement is the real gate either way.
const GET_TOKEN_TIMEOUT_MS = 4000;

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("appCheckHeader: getToken timed out")), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (err) => {
        clearTimeout(timer);
        reject(err);
      },
    );
  });
}

/**
 * Mirrors shared/api/client.ts's appCheckHeader() for the mobile apps: fails
 * open (returns {}) whenever App Check isn't configured for this environment
 * or a token can't be fetched, so a missing/broken web attestation setup
 * never blocks a request client-side — the backend's own enforcement
 * (FirebaseAppCheckMiddleware) decides whether to accept or reject it.
 */
export async function appCheckHeader(): Promise<Record<string, string>> {
  if (typeof window === "undefined") return {}; // App Check is browser-only; no-op during SSR/build
  const instance = getAppCheckInstance();
  if (!instance) return {};
  try {
    const { token } = await withTimeout(getToken(instance, /* forceRefresh */ false), GET_TOKEN_TIMEOUT_MS);
    return token ? { "X-Firebase-AppCheck": token } : {};
  } catch {
    return {};
  }
}
