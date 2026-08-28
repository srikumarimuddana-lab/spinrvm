module.exports = {
  preset: 'jest-expo',
  // Raised from Jest's 5000ms default 2026-08-24: no individual test hangs
  // (every affected test passes standalone and in a plain local full-suite
  // run) — three DIFFERENT test files across this app and rider-app
  // (driverProfileScreen here, rideOptions/homeScreen in rider-app)
  // intermittently exceeded 5000ms one at a time in CI's coverage-
  // instrumented full-suite run as the repo's suite size grew, each only
  // under real CPU contention on the runner. Bumping the same file's
  // timeout each time it was that file's turn was whack-a-mole; fixing the
  // shared default addresses the actual cause. See rider-app/jest.config.js
  // for the matching change.
  testTimeout: 15000,
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
      // ACTION_ITEMS.md B37 milestone ratchet (see
      // docs/testing/coverage-ratchet-plan.md): the extensive app/-screen
      // test-authoring work logged in ACTION_ITEMS.md's B37 entry moved
      // real coverage far past the last-set threshold below without the
      // gate ever being tightened to track it. Fresh measurement
      // 2026-08-24 (`npx jest --coverage`, 115/115 suites, 1243/1243
      // tests): lines 67.37%, statements 65.73%, functions 63.25%,
      // branches 57.45% (no `branches` key here, matching this config's
      // pre-existing pattern). Threshold raised to ~2-3pts below that
      // measured ceiling -- a real regression tripwire again instead of a
      // stale floor 30+pts under actual. Next ratchet step per the plan
      // doc.
      lines: 65,
      functions: 60,
      statements: 63,
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
    // Same class again for RN 0.87: its types moved behind the exports map
    // (types_generated/index.d.ts, no top-level "types" field), so tsconfig now
    // paths "react-native" -> that .d.ts for tsc. jest-expo converts the path
    // too; override back to the real runtime package.
    '^react-native$': '<rootDir>/node_modules/react-native',
    // Expo SDK 54 winter (WinterCG) runtime executes require('./ImportMetaRegistry')
    // lazily via installGlobal — that require fails inside Jest's sandbox.
    // Mock the index so jest-expo's setup.js gets a no-op instead of the real runtime.
    '^expo/src/winter$': '<rootDir>/__mocks__/expo-winter-runtime.js',
    '^expo/src/winter/ImportMetaRegistry$': '<rootDir>/__mocks__/expo-winter-runtime.js',
    // Sentry needs native modules; tests exercise the errorReporting facade
    // against this stub instead.
    '^@sentry/react-native$': '<rootDir>/__mocks__/sentry-react-native.js',
    // Pure geometry — use the real module so marker tests exercise real math.
    '^@shared/utils/vehicleTracking$': '<rootDir>/../shared/utils/vehicleTracking.ts',
    '^@shared/api/client$': '<rootDir>/__mocks__/@shared/api/client.js',
    '^@shared/config/spinr\\.config$': '<rootDir>/__mocks__/@shared/config/spinr.config.js',
    '^@shared/(.*)$': '<rootDir>/__mocks__/@shared/$1',
  },
};
