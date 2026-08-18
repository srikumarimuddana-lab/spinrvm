// Jest maps every `@shared/*` import into this tree (jest.config.js
// moduleNameMapper), so a unit test that reaches shared/services/firebase.ts
// needs a stub — the real module touches @react-native-firebase native modules
// at import time.
//
// Deliberately inert: every function resolves to a benign value rather than
// throwing, so a test that only passes THROUGH this module doesn't have to know
// it exists. A suite that actually asserts on Firebase behaviour should
// jest.mock() it with its own factory, which overrides this.
module.exports = {
  __esModule: true,
  initFirebaseServices: jest.fn(() => Promise.resolve()),
  requestNotificationPermission: jest.fn(() => Promise.resolve(false)),
  checkNotificationPermission: jest.fn(() =>
    Promise.resolve({ granted: false, canAskAgain: true }),
  ),
  openNotificationSettings: jest.fn(() => Promise.resolve()),
  requestAndroidNotificationPermission: jest.fn(() => Promise.resolve(false)),
  requestPushPermissionAndGetToken: jest.fn(() => Promise.resolve(null)),
  onForegroundMessage: jest.fn(() => jest.fn()),
  setBackgroundMessageHandler: jest.fn(),
  onTokenRefresh: jest.fn(() => jest.fn()),
  logCrashlyticsEvent: jest.fn(),
  setCrashlyticsUser: jest.fn(),
  recordError: jest.fn(),
  getAppCheckToken: jest.fn(() => Promise.resolve(null)),
};
