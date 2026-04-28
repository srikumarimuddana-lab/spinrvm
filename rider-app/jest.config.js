module.exports = {
  preset: 'jest-expo',
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
    '^@shared/(.*)$': '<rootDir>/../shared/$1',
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
