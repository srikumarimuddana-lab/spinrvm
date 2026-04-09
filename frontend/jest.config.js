module.exports = {
  preset: 'jest-expo',
  setupFiles: ['./jest.setup.js'],
  testPathIgnorePatterns: ['/node_modules/', '/e2e/'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  collectCoverageFrom: [
    'store/**/*.{ts,tsx}',
    'api/**/*.{ts,tsx}',
    'components/**/*.{ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
  ],
  moduleNameMapper: {
    '^@shared/cache$': '<rootDir>/__mocks__/@shared/cache.js',
    '^@shared/cache/(.*)$': '<rootDir>/__mocks__/@shared/cache.js',
  },
};
