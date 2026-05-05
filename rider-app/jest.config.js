module.exports = {
  preset: 'jest-expo',
  setupFiles: ['./jest-setup-expo.js'],
  setupFilesAfterEnv: ['@testing-library/jest-native/extend-expect'],
  testPathIgnorePatterns: [
    '/node_modules/',
    '/e2e/', // Playwright specs — must be run via `yarn playwright test`, not Jest
    '\\.e2e\\.', // Exclude *.e2e.ts files (Playwright tests)
  ],
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?)|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@unimodules/.*|unimodules|sentry-expo|native-base|react-native-svg|@shared/.*)'
  ],
  modulePaths: ['<rootDir>/node_modules'],
  moduleNameMapper: {
    // jest-expo reads tsconfig paths and converts them to moduleNameMapper. Our tsconfig
    // maps "react" → @types/react so TypeScript resolves shared/ declarations; override
    // here so Jest uses the actual runtime package instead of the type stubs.
    '^react$': '<rootDir>/node_modules/react',
    // Expo SDK 54 winter (WinterCG) runtime executes require('./ImportMetaRegistry')
    // lazily via installGlobal — that require fails inside Jest's sandbox.
    // Mock the index so jest-expo's setup.js gets a no-op instead of the real runtime.
    '^expo/src/winter$': '<rootDir>/__mocks__/expo-winter-runtime.js',
    '^expo/src/winter/ImportMetaRegistry$': '<rootDir>/__mocks__/expo-winter-runtime.js',
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
