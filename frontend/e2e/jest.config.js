/** @type {import('@jest/types').Config.InitialOptions} */
module.exports = {
  rootDir: '..',
  testMatch: ['<rootDir>/e2e/**/*.test.ts?(x)'],
  testTimeout: 120000,
  maxWorkers: 1,
  globalSetup: 'detox/runners/jest/globalSetup',
  globalTeardown: 'detox/runners/jest/globalTeardown',
  reportSpecs: true,
  testRunner: 'jest-circus/runner',
  verbose: true,
  preset: 'ts-jest',
  setupFilesAfterEnv: ['<rootDir>/e2e/init.js'],
  transform: {
    '^.+\\.(ts|tsx)$': 'ts-jest',
  },
};