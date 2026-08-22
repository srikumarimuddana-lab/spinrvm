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
  // ACTION_ITEMS.md B37 sub-item 2: was scoped to store/ only (6 of ~96
  // source files) -- widened 2026-08-22 to also include hooks/ and utils/
  // (smaller, more unit-testable than app/ screens, which are covered by
  // e2e instead and are the next widening step). app/, components/,
  // lib/, services/ remain outside collectCoverageFrom for now.
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
  ],
  coverageThreshold: {
    global: {
      // ACTION_ITEMS.md B37: dropped to real-measured-minus-headroom for
      // the widened file set. Measured 2026-08-22 after adding hooks/ and
      // utils/: lines 68.09%, functions 64.62%, branches 56.79%. Set a few
      // points below so the gate lands green, not red on main. Ratchet up
      // as tests are added -- do not raise without re-measuring first.
      lines: 63,
      functions: 60,
      branches: 52,
    },
  },
};
