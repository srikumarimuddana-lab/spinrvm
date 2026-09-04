import { Platform } from 'react-native';
import Constants from 'expo-constants';

// Hardcoded production URL — used as the final safety net when all env-var
// / config paths fail. OTA updates (eas update) sometimes ship without
// EXPO_PUBLIC_BACKEND_URL if Metro didn't load the .env, which silently
// falls through to http://localhost:8000 and breaks every request on a
// real device. This constant ensures production builds always reach the
// real backend even when the env chain is empty.
//
// Must be the load-balanced CNAME (api-spinr.spinr.ca), NOT a provider domain.
// Pointing it at a provider host (e.g. *.up.railway.app) would make this
// fallback bypass the CNAME, so Fly-primary cutover and fail-back drills would
// never reach clients that hit the safety net. See ADR-007.
const PRODUCTION_BACKEND_URL = 'https://api-spinr.spinr.ca';

const getBackendUrl = () => {
  const getEnvVar = (key: string): string | undefined => {
    try {
      return process.env[key];
    } catch {
      return undefined;
    }
  };

  // 1. Prefer explicit env var — set EXPO_PUBLIC_BACKEND_URL in your .env file
  const backendUrl = getEnvVar('EXPO_PUBLIC_BACKEND_URL');
  if (backendUrl) {
    console.log('[SpinrConfig] Backend URL from env:', backendUrl);
    return backendUrl;
  }

  // 2. Generic API URL fallback
  const apiUrl = getEnvVar('EXPO_PUBLIC_API_URL');
  if (apiUrl) {
    console.log('[SpinrConfig] Backend URL from EXPO_PUBLIC_API_URL:', apiUrl);
    return apiUrl;
  }

  // 3. Expo Go / Dev Client: auto-detect the host machine's IP from Expo's metadata.
  if (Constants.expoConfig?.hostUri) {
    let host = Constants.expoConfig.hostUri.split(':')[0];
    if (Platform.OS === 'android' && (host === '127.0.0.1' || host === 'localhost')) {
      host = '10.0.2.2';
    }
    const generatedUrl = `http://${host}:8000`;
    console.log('[SpinrConfig] Backend URL auto-detected from Expo hostUri:', generatedUrl);
    return generatedUrl;
  }

  // 4. Expo extra config (set in app.config.ts extra field)
  const extraUrl = Constants.expoConfig?.extra?.EXPO_PUBLIC_BACKEND_URL || Constants.expoConfig?.extra?.backendUrl;
  if (extraUrl) {
    console.log('[SpinrConfig] Backend URL from app.config extra:', extraUrl);
    return extraUrl;
  }

  // 5. Production safety net — if none of the env / config paths resolved,
  // use the hardcoded production URL. This prevents OTA updates from
  // silently falling through to localhost (which is unreachable on real
  // devices and causes "Network request failed" on every API call).
  if (!__DEV__) {
    console.warn('[SpinrConfig] No env var found — using hardcoded production URL');
    return PRODUCTION_BACKEND_URL;
  }

  // 6. Dev-only fallback for Android emulator.
  if (Platform.OS === 'android') {
    console.warn('[SpinrConfig] Backend URL: falling back to Android emulator alias 10.0.2.2');
    return 'http://10.0.2.2:8000';
  }

  console.error(
    '[SpinrConfig] Could not determine backend URL! ' +
    'Set EXPO_PUBLIC_BACKEND_URL in your .env file.',
  );
  return 'http://localhost:8000';
};

// HTTPS-only enforcement for production builds. A non-dev build must never
// talk to the backend over cleartext HTTP — that would expose live location,
// trip state, payment, and identity-document traffic to any on-path attacker
// (rogue Wi-Fi, captive portal, proxy). If the resolved URL is somehow http://
// in a release build, fall back to the known production HTTPS endpoint rather
// than sending sensitive traffic in the clear.
// (Certificate pinning is a native-build concern handled via the @react-native
// networking config; this guard covers the transport-scheme half.)
const enforceHttps = (url: string): string => {
  if (__DEV__) return url;
  if (url.startsWith('https://')) return url;
  console.error(
    `[SpinrConfig] Refusing cleartext backend URL in production build: ${url} ` +
    `— falling back to ${PRODUCTION_BACKEND_URL}`,
  );
  return PRODUCTION_BACKEND_URL;
};

export const SpinrConfig = {
  backendUrl: enforceHttps(getBackendUrl()),
  // App Info
  app: {
    name: 'Spinr',
    version: '1.0.0',
    region: 'CA', // Canada
  },

  // Design System
  // `colors` used to live here and was removed 2026-09-04: it had drifted
  // from shared/theme/index.ts (the real, canonical, useTheme()-served
  // palette — e.g. this block's `warning: '#FFCC00'` vs. theme/index.ts's
  // `warning: '#d97706'`/`'#F59E0B'`, same token holding two different
  // values). Traced every consumer first: nothing in either app's live,
  // rendered UI read colors from here — the only reader was
  // driver-app/components/DriverTopBar.tsx, a dead file never imported
  // anywhere (removed in the same change). Colors now have exactly one
  // source of truth: shared/theme/index.ts via useTheme().
  theme: {
    borderRadius: 16,
    fontFamily: 'PlusJakartaSans',
  },

  // Canadian Cities (Saskatchewan)
  cities: [
    { label: 'Saskatoon', value: 'Saskatoon' },
    { label: 'Regina', value: 'Regina' },
  ],

  // Phone Configuration
  phone: {
    countryCode: '+1',
    placeholder: '(306) 555-0199',
    // Canadian phone regex pattern
    pattern: /^\+1\s?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}$/,
  },

  // OTP Configuration
  otp: {
    length: 4, // 4-digit phone-verification OTP (backend-issued)
    expiryMinutes: 5,
  },

  // Ride Offer Configuration
  rideOffer: {
    countdownSeconds: 15, // Time to accept/decline ride
    maxRadiusMeters: 5000, // Max distance for nearby drivers
  },

  // ============================================
  // FIREBASE CONFIGURATION (Update when ready)
  // ============================================
  firebase: {
    enabled: false, // Set to true when Firebase is configured
    apiKey: '',
    authDomain: '',
    projectId: '',
    storageBucket: '',
    messagingSenderId: '',
    appId: '',
  },

  // ============================================
  // TWILIO CONFIGURATION (Update when ready)
  // ============================================
  twilio: {
    enabled: false, // Set to true when Twilio is configured
    // Note: Twilio credentials should be on backend only
    // This is just a flag to switch between mock and real SMS
  },
};

export default SpinrConfig;
