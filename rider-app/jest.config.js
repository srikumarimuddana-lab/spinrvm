module.exports = {
  preset: 'jest-expo',
  setupFiles: ['<rootDir>/__mocks__/expo-jest-setup.js'],
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
    '^@shared/(.*)$': '<rootDir>/../shared/$1',
    // Jest 30 + Expo SDK 54: the winter runtime installs global getters for
    // import.meta polyfills. The lazy require() inside those getters is called
    // through closures that jest-runtime 30 does not recognise as "inside test
    // code", triggering isInsideTestCode === false and a ReferenceError.
    // Stub the entire winter side-effect chain — none of it is needed under Jest.
    'expo/src/winter/ImportMetaRegistry': '<rootDir>/__mocks__/expo-winter-stub.js',
    'expo/src/winter/runtime(\\.native)?': '<rootDir>/__mocks__/expo-winter-stub.js',
    'expo/src/winter/index': '<rootDir>/__mocks__/expo-winter-stub.js',
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
