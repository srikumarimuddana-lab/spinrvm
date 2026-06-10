// Mock for @shared/services/errorReporting (driver-app jest maps all
// @shared/* imports into __mocks__/@shared/). Keeps tests that mount
// app/_layout.tsx hermetic — no Sentry/Crashlytics side effects.
module.exports = {
  initErrorReporting: jest.fn(),
  isSentryActive: jest.fn(() => false),
  captureException: jest.fn(),
  captureMessage: jest.fn(),
  setUser: jest.fn(),
  addBreadcrumb: jest.fn(),
  _resetForTests: jest.fn(),
};
