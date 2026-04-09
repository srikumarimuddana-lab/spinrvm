module.exports = {
  preset: 'jest-expo',
  setupFiles: ['./jest.setup.js'],
  testPathIgnorePatterns: ['/node_modules/', '/e2e/'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx'],
  collectCoverageFrom: [
    'store/**/*.{ts,tsx}',
    'components/**/*.{ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
  ],
  moduleNameMapper: {
    '^@shared/api/client$': '<rootDir>/__mocks__/@shared/api/client.js',
    '^@shared/config/spinr\\.config$': '<rootDir>/__mocks__/@shared/config/spinr.config.js',
    '^@shared/(.*)$': '<rootDir>/__mocks__/@shared/$1',
  },
};
