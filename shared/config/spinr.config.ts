import { Platform } from 'react-native';
import Constants from 'expo-constants';

const getBackendUrl = () => {
  // 1. Prefer Environment Variable (Prod / CI)
  if (process.env.EXPO_PUBLIC_BACKEND_URL) {
    return process.env.EXPO_PUBLIC_BACKEND_URL;
  }

  // 2. Fallback to API URL or Base URL generically
  if (process.env.EXPO_PUBLIC_API_URL) {
    return process.env.EXPO_PUBLIC_API_URL;
  }

  // 3. Expo Go / Dev Client (Physical Device or Emulator)
  // This automatically grabs the IP of the machine running `npx expo start`
  if (Constants.expoConfig?.hostUri) {
    const host = Constants.expoConfig.hostUri.split(':')[0];
    return `http://${host}:8000`;
  }

  console.warn("No EXPO_PUBLIC_BACKEND_URL or EXPO_PUBLIC_API_URL provided!");
  return '';
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
    length: 4, // 4-digit for driver app
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
