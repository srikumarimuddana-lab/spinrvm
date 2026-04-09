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
