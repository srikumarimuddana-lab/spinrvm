module.exports = {
  preset: 'jest-expo',
  setupFiles: ['./jest.setup.js'],
  testPathIgnorePatterns: ['/node_modules/', '/e2e/'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  // Resolve babel-runtime helpers and other hoisted deps from driver-app's
  // node_modules even when the file under test lives outside rootDir (e.g.
  // ../shared/**). Without this, tests that import real shared modules fail
  // with "Cannot find module '@babel/runtime/helpers/...'" because shared/
  // has no node_modules of its own.
  modulePaths: ['<rootDir>/node_modules'],
  transformIgnorePatterns: [
    'node_modules/(?!((jest-)?react-native|@react-native(-community)?)|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|@unimodules/.*|unimodules|sentry-expo|native-base|react-native-svg|@shared/.*)'
  ],
  // ACTION_ITEMS.md B37: was scoped to store/+components/ only (29 of
  // ~116 source files) -- widened 2026-08-22 in four steps: first
  // hooks/+utils/ (smaller, more unit-testable), then app/ (the actual
  // screens), then lib/+services/, then api/ (its only file, client.ts).
  // Every top-level source directory in this app is now measured.
  collectCoverageFrom: [
    'store/**/*.{ts,tsx}',
    'components/**/*.{ts,tsx}',
    'hooks/**/*.{ts,tsx}',
    'utils/**/*.{ts,tsx}',
    'app/**/*.{ts,tsx}',
    'lib/**/*.{ts,tsx}',
    'services/**/*.{ts,tsx}',
    'api/**/*.{ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
    '!**/__tests__/**',
  ],
  coverageThreshold: {
    global: {
      // ACTION_ITEMS.md B37: tightened to a near-ceiling floor 2026-08-22
      // (user asked to "raise the thresholds toward the real ceiling").
      // Raised a third time same day after adding a second test file for
      // services/backgroundMessaging.ts's Android/Notifee branches
      // (__tests__/services/backgroundMessaging.android.test.ts) -- the
      // existing test file deliberately stays on Platform.OS='ios' to
      // exercise only the persist+republish path, leaving the Android
      // notification render, the location_health recovery branch, and
      // notifee.onBackgroundEvent's accept/decline/tap routing (a lock-
      // screen decline while the app is killed is the ONLY place that
      // reaches the backend headlessly) entirely untested.
      // backgroundMessaging.ts: 48.57%/58.33%/46.95%
      // (lines/functions/branches) -> 96.19%/100%/88.69%. Fresh
      // aggregate: lines 34.62%, statements 33.66%, functions 27.05%
      // (no `branches` key here, matching this config's pre-existing
      // pattern; up from 33.9%/32.91%/26.77%). Raising further requires
      // more new tests, not just re-measuring -- this is NOT the user's
      // stated 100% target, only the honest ceiling of what's currently
      // tested.
      lines: 33,
      functions: 26,
      statements: 32,
    },
  },
  moduleNameMapper: {
    // Redirect `require('react')` to the runtime package, not @types/react (which has no exports).
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
    '^expo/src/winter$': '<rootDir>/__mocks__/expo-winter-runtime.js',
    '^expo/src/winter/ImportMetaRegistry$': '<rootDir>/__mocks__/expo-winter-runtime.js',
    // Sentry needs native modules; tests exercise the errorReporting facade
    // against this stub instead.
    '^@sentry/react-native$': '<rootDir>/__mocks__/sentry-react-native.js',
    '^@shared/api/client$': '<rootDir>/__mocks__/@shared/api/client.js',
    '^@shared/config/spinr\\.config$': '<rootDir>/__mocks__/@shared/config/spinr.config.js',
    '^@shared/(.*)$': '<rootDir>/__mocks__/@shared/$1',
  },
};
