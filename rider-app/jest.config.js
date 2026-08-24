module.exports = {
  preset: 'jest-expo',
  // Raised from Jest's 5000ms default 2026-08-24: no individual test hangs
  // (every affected test passes standalone and in a plain local full-suite
  // run) — three DIFFERENT test files (driverProfileScreen, rideOptions,
  // homeScreen — the last two in this app) intermittently exceeded 5000ms
  // one at a time in CI's coverage-instrumented full-suite run as the repo's
  // suite size grew, each only under real CPU contention on the runner.
  // Bumping the same file's timeout each time it was that file's turn was
  // whack-a-mole; fixing the shared default addresses the actual cause.
  testTimeout: 15000,
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
    // tsconfig paths include "react" → "@types/react" to fix TypeScript resolution for
    // shared/ files; jest-expo converts tsconfig paths to moduleNameMapper, which would
    // redirect require('react') to a types-only package with no runtime code.
    // Override here to always use the real react module for tests.
    '^react$': '<rootDir>/node_modules/react',
    // Same class of bug as the 'react' override above, introduced by the SDK 57
    // upgrade's tsconfig.base switching jsx mode to "react-jsx": tsconfig paths now
    // also map "react/jsx-runtime"/"react/jsx-dev-runtime" -> "@types/react/..." (a
    // .d.ts-only path, needed so tsc resolves the automatic jsx runtime for files in
    // the sibling shared/ package). jest-expo converts those paths too, so
    // require('react/jsx-runtime') from the actual automatic-jsx-transform output
    // would resolve to a type declaration file with no runtime code. Override back to
    // the real runtime modules for tests.
    '^react/jsx-runtime$': '<rootDir>/node_modules/react/jsx-runtime',
    '^react/jsx-dev-runtime$': '<rootDir>/node_modules/react/jsx-dev-runtime',
    // Expo SDK 54 winter (WinterCG) runtime executes require('./ImportMetaRegistry')
    // lazily via installGlobal — that require fails inside Jest's sandbox.
    // Mock the index so jest-expo's setup.js gets a no-op instead of the real runtime.
    // Sentry needs native modules; the errorReporting facade is tested
    // against this stub instead.
    '^@sentry/react-native$': '<rootDir>/__mocks__/sentry-react-native.js',
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
  // ACTION_ITEMS.md B37: was scoped to store/ only (6 of ~96 source
  // files) -- widened 2026-08-22 in four steps: hooks/+utils/ (smaller,
  // more unit-testable), then app/ (the actual screens), then
  // components/, then lib/+services/. api/ remains outside
  // collectCoverageFrom.
  collectCoverageFrom: [
    'store/**/*.ts',
    '!store/**/__tests__/**',
    '!store/**/*.d.ts',
    '!store/workProfileStore.ts',
    'hooks/**/*.{ts,tsx}',
    '!hooks/**/__tests__/**',
    '!hooks/**/*.d.ts',
    'utils/**/*.{ts,tsx}',
    '!utils/**/__tests__/**',
    '!utils/**/*.d.ts',
    'app/**/*.{ts,tsx}',
    '!app/**/__tests__/**',
    '!app/**/*.d.ts',
    'components/**/*.{ts,tsx}',
    '!components/**/__tests__/**',
    '!components/**/*.d.ts',
    'lib/**/*.{ts,tsx}',
    '!lib/**/__tests__/**',
    '!lib/**/*.d.ts',
    'services/**/*.{ts,tsx}',
    '!services/**/__tests__/**',
    '!services/**/*.d.ts',
  ],
  coverageThreshold: {
    global: {
      // ACTION_ITEMS.md B37: tightened to a near-ceiling floor 2026-08-22
      // (user asked to "raise the thresholds toward the real ceiling").
      // Raised again same day after adding real tests for all 5 files in
      // lib/+services/ that previously had zero coverage (alert.ts,
      // notifyError.ts, shareTripMessage.ts, rideLiveNotification.ts,
      // rideVoltraLiveActivity.ts -- each now at or near 100%). Fresh
      // measurement: lines 21.37%, statements 21.28%, functions 17.85%,
      // branches 16.64% (up from 20.08%/19.96%/16.77%/15.47%). Raising
      // further requires more new tests, not just re-measuring -- this is
      // NOT the user's stated 100% target, only the honest ceiling of
      // what's currently tested.
      lines: 20,
      functions: 16,
      branches: 15,
    },
  },
};
