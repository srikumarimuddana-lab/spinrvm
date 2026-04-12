import { Platform } from 'react-native';
import Constants from 'expo-constants';

const getBackendUrl = () => {
  // Safely access environment variables (compatible with Expo SDK 55)
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
    console.log('Backend URL from env:', backendUrl);
    return backendUrl;
  }

  // 2. Generic API URL fallback
  const apiUrl = getEnvVar('EXPO_PUBLIC_API_URL');
  if (apiUrl) {
    console.log('Backend URL from EXPO_PUBLIC_API_URL:', apiUrl);
    return apiUrl;
  }

  // 3. Expo Go / Dev Client: auto-detect the host machine's IP from Expo's metadata.
  // Constants.expoConfig.hostUri is set by `npx expo start` and contains the LAN IP,
  // so this works on physical devices without needing a hardcoded IP.
  if (Constants.expoConfig?.hostUri) {
    let host = Constants.expoConfig.hostUri.split(':')[0];
    // On Android emulator, the special alias 10.0.2.2 routes to the host machine.
    if (Platform.OS === 'android' && (host === '127.0.0.1' || host === 'localhost')) {
      host = '10.0.2.2';
    }
    const generatedUrl = `http://${host}:8000`;
    console.log('Backend URL auto-detected from Expo hostUri:', generatedUrl);
    return generatedUrl;
  }

  // 4. Expo extra config (set in app.config.ts extra field)
  const extraUrl = Constants.expoConfig?.extra?.EXPO_PUBLIC_BACKEND_URL || Constants.expoConfig?.extra?.backendUrl;
  if (extraUrl) {
    console.log('Backend URL from app.config extra:', extraUrl);
    return extraUrl;
  }

  // 5. Last resort for Android emulator when no hostUri is available.
  if (Platform.OS === 'android') {
    console.warn('Backend URL: falling back to Android emulator alias 10.0.2.2');
    return 'http://10.0.2.2:8000';
  }

  // 6. Nothing worked — log a clear error so it's obvious something is misconfigured.
  console.error(
    '[SpinrConfig] Could not determine backend URL! ' +
    'Set EXPO_PUBLIC_BACKEND_URL in your .env file (e.g. http://192.168.x.x:8000).'
  );
  return 'http://localhost:8000'; // web-only fallback, fails on real devices — fix your .env!
};

export const SpinrConfig = {
  backendUrl: getBackendUrl(),
  // App Info
  app: {
    name: 'Spinr',
    version: '1.0.0',
    region: 'CA', // Canada
  },

  // Design System
  theme: {
    colors: {
      primary: '#FF3B30', // Vibrant Red
      primaryDark: '#D32F2F',
      background: '#FFFFFF',
      surface: '#FFFFFF',
      surfaceLight: '#F5F5F5',
      text: '#1A1A1A',
      textDim: '#666666',
      textSecondary: '#6B7280',
      border: '#E5E7EB',
      error: '#DC2626',
      success: '#34C759', // Green for success
      warning: '#FFCC00',

      // Aliases & Legacy Support
      accent: '#FF3B30',
      accentDim: '#D32F2F',
      danger: '#DC2626',
      orange: '#FF9500',
      gold: '#FFD700',
      overlay: 'rgba(255, 255, 255, 0.95)',
    },
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
    length: 6, // 6-digit phone-verification OTP (backend-issued)
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
