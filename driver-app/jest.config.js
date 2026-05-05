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
  collectCoverageFrom: [
    'store/**/*.{ts,tsx}',
    'components/**/*.{ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
  ],
  coverageThreshold: {
    global: {
      // P3-4: step +5 pp/sprint toward 50/40/50 target.
      // Actuals at 2026-04-28: lines≈35%, functions≈26%, statements≈35%.
      // Next step: 2026-07 sprint → 40/30/40.
      lines: 35,
      functions: 25,
      statements: 34,
    },
  },
  moduleNameMapper: {
    // Redirect `require('react')` to the runtime package, not @types/react (which has no exports).
    '^react$': '<rootDir>/node_modules/react',
    // Expo SDK 54 winter (WinterCG) runtime executes require('./ImportMetaRegistry')
    // lazily via installGlobal — that require fails inside Jest's sandbox.
    // Mock the index so jest-expo's setup.js gets a no-op instead of the real runtime.
    '^expo/src/winter$': '<rootDir>/__mocks__/expo-winter-runtime.js',
    '^expo/src/winter/ImportMetaRegistry$': '<rootDir>/__mocks__/expo-winter-runtime.js',
    '^@shared/api/client$': '<rootDir>/__mocks__/@shared/api/client.js',
    '^@shared/config/spinr\\.config$': '<rootDir>/__mocks__/@shared/config/spinr.config.js',
    '^@shared/(.*)$': '<rootDir>/__mocks__/@shared/$1',
  },
};
