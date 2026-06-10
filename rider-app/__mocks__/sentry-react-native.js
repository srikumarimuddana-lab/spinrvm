// Jest stub for @sentry/react-native. The real package needs native modules
// (and a yarn.lock entry); unit tests only assert how the errorReporting
// facade drives the SDK surface below.
module.exports = {
  init: jest.fn(),
  captureException: jest.fn(),
  captureMessage: jest.fn(),
  setUser: jest.fn(),
  addBreadcrumb: jest.fn(),
  wrap: jest.fn((c) => c),
};
