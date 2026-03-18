/**
 * Detox E2E Test Initialization
 */
const detox = require('detox');
const config = require('../package.json').detox;

beforeAll(async () => {
  await detox.init(config);
  await device.launchApp({
    permissions: {
      location: 'always',
      notifications: 'YES',
    },
  });
});

beforeEach(async () => {
  await device.reloadReactNative();
});

afterAll(async () => {
  await detox.cleanup();
});