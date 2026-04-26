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
    '^@shared/(.*)$': '<rootDir>/../shared/$1',
  },
  // R-P1-22: Enforce minimum coverage thresholds before merging to main.
  // Scoped to store/ files where unit tests exist; app screens are covered by e2e.
  collectCoverageFrom: [
    'store/**/*.ts',
    '!store/**/__tests__/**',
    '!store/**/*.d.ts',
  ],
  coverageThreshold: {
    global: {
      lines: 60,
      functions: 55,
      branches: 40,
    },
  },
};
