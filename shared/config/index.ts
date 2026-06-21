import Constants from 'expo-constants';

// Hardcoded production safety net — must stay in sync with the same constant in
// spinr.config.ts. The load-balanced CNAME (NOT a provider host) so Fly-primary
// cutover / fail-back still reaches clients that hit the fallback. See ADR-007.
const PRODUCTION_BACKEND_URL = 'https://api-spinr.spinr.ca';

/**
 * Resolve the Spinr backend URL for the current runtime.
 *
 * Priority (first win):
 *
 *   1. EXPO_PUBLIC_BACKEND_URL  — explicit, preferred
 *   2. EXPO_PUBLIC_API_URL      — legacy alias some screens still read
 *   3. Expo hostUri             — auto-detects the LAN IP from Metro so
 *                                 physical devices connect to the dev host
 *                                 without hardcoding an IP
 *
 * If none of the above resolve, we return `null` and log a loud error.
 * Previously this fallback was `https://spinr-backend.onrender.com` —
 * a stale production URL from a since-abandoned deploy. That meant a
 * misconfigured build would silently hit the wrong backend, log 404s
 * for every API call, and leave operators chasing ghosts. Failing
 * loudly is better than silently hitting the wrong host.
 *
 * Callers should treat `null` as "not configured" and surface an
 * error to the user rather than attempting requests.
 */
const getBackendUrl = (): string => {
    if (process.env.EXPO_PUBLIC_BACKEND_URL) {
        return process.env.EXPO_PUBLIC_BACKEND_URL;
    }
    if (process.env.EXPO_PUBLIC_API_URL) {
        return process.env.EXPO_PUBLIC_API_URL;
    }
    if (Constants.expoConfig?.hostUri) {
        const host = Constants.expoConfig.hostUri.split(':')[0];
        return `http://${host}:8000`;
    }
    // Production safety net. A release build that shipped without
    // EXPO_PUBLIC_BACKEND_URL (e.g. EAS env not set, or an OTA update where
    // Metro didn't load .env) must NOT fall through to an empty string: an
    // empty API_URL makes the rider/driver WebSocket URL scheme-less
    // ("/ws/rider/..."), which the native WebSocket module rejects with a
    // FATAL IllegalArgumentException and crashes the whole app on launch.
    // Mirror spinr.config.ts and return the load-balanced production CNAME so
    // HTTP and WebSocket both reach the real backend. (Dev still fails loudly.)
    if (typeof __DEV__ === 'undefined' || !__DEV__) {
        console.warn('[SpinrConfig] No backend env var — using hardcoded production URL');
        return PRODUCTION_BACKEND_URL;
    }
    console.error(
        '[SpinrConfig] Backend URL not configured. Set EXPO_PUBLIC_BACKEND_URL ' +
        'in your .env file (e.g. http://192.168.x.x:8000 for LAN dev, or your ' +
        'deployed backend URL for production builds).'
    );
    return '';
};

export const API_URL = getBackendUrl();
