module.exports = {
  preset: 'jest-expo',
  setupFiles: ['./jest-setup-expo.js'],
  setupFilesAfterEnv: ['@testing-library/jest-native/extend-expect'],
  testPathIgnorePatterns: [
    '/node_modules/',
    '/e2e/', // Playwright specs — must be run via `yarn playwright test`, not Jest
  ],
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?)|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@unimodules/.*|unimodules|sentry-expo|native-base|react-native-svg|@shared/.*)'
  ],
  modulePaths: ['<rootDir>/node_modules'],
  moduleNameMapper: {
    // expo/src/winter installs polyfills via a lazy getter that fires outside
    // test-code scope (isInsideTestCode=false), which Jest 30 blocks with a
    // ReferenceError. Node 22 ships TextDecoder, URL, structuredClone etc.
    // natively, so skipping the expo polyfill setup is safe in tests.
    '^expo/src/winter$': '<rootDir>/__mocks__/expoWinter.js',
    '^expo/src/winter/ImportMetaRegistry$': '<rootDir>/__mocks__/expo-winter-noop.js',
    '^@shared/(.*)$': '<rootDir>/../shared/$1',
    // Expo SDK 54 winter (WinterCG) runtime executes require('./ImportMetaRegistry')
    // lazily via installGlobal — that require fails inside Jest's sandbox.
    // Mock the index so jest-expo's setup.js gets a no-op instead of the real runtime.
    '^expo/src/winter$': '<rootDir>/__mocks__/expo-winter-runtime.js',
    '^expo/src/winter/ImportMetaRegistry$': '<rootDir>/__mocks__/expo-winter-runtime.js',
  },
  // R-P1-22: Enforce minimum coverage thresholds before merging to main.
  // Scoped to store/ files where unit tests exist; app screens are covered by e2e.
  collectCoverageFrom: [
    'store/**/*.ts',
    '!store/**/__tests__/**',
    '!store/**/*.d.ts',
    '!store/workProfileStore.ts',
  ],
  coverageThreshold: {
    global: {
      lines: 58,
      functions: 50,
      branches: 40,
    },
  },
};
