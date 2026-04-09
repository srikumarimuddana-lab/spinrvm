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

// Mock @react-native-async-storage/async-storage
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));

// Mock firebase config
jest.mock('./config/firebaseConfig', () => ({
  auth: {
    onAuthStateChanged: jest.fn(),
    currentUser: null,
  },
}));

// Mock firebase/auth
jest.mock('firebase/auth', () => ({
  PhoneAuthProvider: { credential: jest.fn() },
  signInWithCredential: jest.fn(),
  signOut: jest.fn(),
}));

// Mock shared cache
jest.mock('@shared/cache', () => ({
  appCache: {
    get: jest.fn(() => Promise.resolve(null)),
    set: jest.fn(() => Promise.resolve()),
    clearUserCache: jest.fn(() => Promise.resolve()),
  },
  CACHE_KEYS: {
    USER_PROFILE: 'user_profile',
    DRIVER_PROFILE: 'driver_profile',
  },
  CACHE_CONFIG: {
    USER_PROFILE_TTL: 300000,
  },
}));

// Mock shared cache index
jest.mock('@shared/cache/index', () => ({
  appCache: {
    get: jest.fn(() => Promise.resolve(null)),
    set: jest.fn(() => Promise.resolve()),
    clearUserCache: jest.fn(() => Promise.resolve()),
  },
  CACHE_KEYS: {
    USER_PROFILE: 'user_profile',
    DRIVER_PROFILE: 'driver_profile',
  },
  CACHE_CONFIG: {
    USER_PROFILE_TTL: 300000,
  },
}));

// Silence console.log in tests
jest.spyOn(console, 'log').mockImplementation(() => {});
jest.spyOn(console, 'warn').mockImplementation(() => {});
