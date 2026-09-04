// Expo SDK 54's runtime.native.ts installs lazy getters for multiple globals
// (__ExpoImportMetaRegistry, structuredClone, TextDecoder, URL, etc.) via
// installGlobal.ts. Each getter defers its require() call until first access.
// If that access happens during test-file module loading (isInsideTestCode=false),
// jest-runtime throws a scope error.
//
// Fix: eagerly trigger every lazy getter here, while we're still inside the
// setupFiles execution window (isInsideTestCode=true), so the values are
// cached as plain properties before any test module loads.

const EXPO_WINTER_GLOBALS = [
  '__ExpoImportMetaRegistry',
  'structuredClone',
  'TextDecoder',
  'TextDecoderStream',
  'TextEncoderStream',
  'URL',
  'URLSearchParams',
];

for (const name of EXPO_WINTER_GLOBALS) {
  const desc = Object.getOwnPropertyDescriptor(global, name);
  if (desc && typeof desc.get === 'function') {
    try {
       
      void global[name]; // triggers the lazy getter; defineLazyObjectProperty then replaces it with a static value
    } catch (_e) {
      // getter resolution failed; install a safe stub so test-module loading won't re-trigger
      Object.defineProperty(global, name, {
        value: undefined,
        configurable: true,
        writable: true,
        enumerable: false,
      });
    }
  }
}

// Mock @react-native-async-storage/async-storage — no test in this app
// previously imported shared/theme/ThemeContext.tsx (which requires it at
// module scope for its persisted theme preference), so this gap went
// unnoticed until shared/components/Button.tsx's tests started importing it
// transitively via useTheme(). Same mock driver-app/jest.setup.js already
// carries for the same reason — mirrored here rather than invented fresh.
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));
