// Expo SDK 54's runtime.native.ts installs lazy getters for multiple globals via
// installGlobal.ts. Eagerly trigger them during setupFiles (isInsideTestCode=true)
// so they cache as static values before test module loading begins.
const _EXPO_WINTER_GLOBALS = [
  '__ExpoImportMetaRegistry', 'structuredClone',
  'TextDecoder', 'TextDecoderStream', 'TextEncoderStream',
  'URL', 'URLSearchParams',
];
for (const _name of _EXPO_WINTER_GLOBALS) {
  const _desc = Object.getOwnPropertyDescriptor(global, _name);
  if (_desc && typeof _desc.get === 'function') {
    try { void global[_name]; } catch (_e) {
      Object.defineProperty(global, _name, { value: undefined, configurable: true, writable: true });
    }
  }
}

// Mock expo-secure-store
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(() => Promise.resolve(null)),
  setItemAsync: jest.fn(() => Promise.resolve()),
  deleteItemAsync: jest.fn(() => Promise.resolve()),
}));

// Mock expo-constants
jest.mock('expo-constants', () => ({
  expoConfig: { hostUri: 'localhost:8081' },
}));

// Mock expo-location globally. Its real index.ts pulls in expo's Expo.fx.tsx
// (app-entry / native-module registration side effects), which crashes under
// Jest with "Cannot read properties of undefined (reading 'create')" at
// expo/src/errors/AppEntryNotFound.tsx. Any suite that imports driverStore.ts
// transitively pulls this in via utils/tripLocationRecorder.ts, so mock it
// once here rather than per-test-file (some test files additionally
// jest.mock('expo-location', ...) locally with a narrower shape — that's
// fine, a local mock overrides this default one for that file).
jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  requestBackgroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  getBackgroundPermissionsAsync: jest.fn(() => Promise.resolve({ status: 'granted' })),
  getLastKnownPositionAsync: jest.fn(() => Promise.resolve(null)),
  getCurrentPositionAsync: jest.fn(() => Promise.resolve({
    coords: { latitude: 0, longitude: 0, speed: 0, heading: 0, accuracy: 5, altitude: 0 },
    mocked: false,
    timestamp: 0,
  })),
  watchPositionAsync: jest.fn(() => Promise.resolve({ remove: jest.fn() })),
  startLocationUpdatesAsync: jest.fn(() => Promise.resolve()),
  hasStartedLocationUpdatesAsync: jest.fn(() => Promise.resolve(false)),
  stopLocationUpdatesAsync: jest.fn(() => Promise.resolve()),
  startGeofencingAsync: jest.fn(() => Promise.resolve()),
  stopGeofencingAsync: jest.fn(() => Promise.resolve()),
  Accuracy: { Lowest: 1, Low: 2, Balanced: 3, High: 4, Highest: 5, BestForNavigation: 6 },
  ActivityType: { Other: 1, AutomotiveNavigation: 2, Fitness: 3, OtherNavigation: 4, Airborne: 5 },
  GeofencingEventType: { Enter: 1, Exit: 2 },
}));

// Mock @react-native-async-storage/async-storage
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

// Mock firebase
jest.mock('firebase/auth', () => ({
  PhoneAuthProvider: { credential: jest.fn() },
  signInWithCredential: jest.fn(),
  signOut: jest.fn(),
}));

// Mock @react-native-firebase
jest.mock('@react-native-firebase/app', () => ({}));
jest.mock('@react-native-firebase/messaging', () => () => ({
  getToken: jest.fn(),
  onMessage: jest.fn(),
}));
jest.mock('@react-native-firebase/crashlytics', () => () => ({
  recordError: jest.fn(),
  setAttribute: jest.fn(),
}));

// Mock shared modules
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
  // Same contract as the real helper: backend detail wins, else fallback.
  getApiErrorMessage: jest.fn(
    (err, fallback = 'Something went wrong. Please try again.') =>
      err?.response?.data?.detail || fallback,
  ),
}));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: {
    rideOffer: { countdownSeconds: 15 },
  },
}));

// Silence console.log in tests
jest.spyOn(console, 'log').mockImplementation(() => {});
jest.spyOn(console, 'warn').mockImplementation(() => {});
