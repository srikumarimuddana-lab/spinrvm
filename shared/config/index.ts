import Constants from 'expo-constants';

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
    console.error(
        '[SpinrConfig] Backend URL not configured. Set EXPO_PUBLIC_BACKEND_URL ' +
        'in your .env file (e.g. http://192.168.x.x:8000 for LAN dev, or your ' +
        'deployed backend URL for production builds).'
    );
    // Return an empty string rather than a stale production URL so
    // requests fail fast with a clear "invalid URL" error instead of
    // silently hitting a wrong host.
    return '';
};

export const API_URL = getBackendUrl();
