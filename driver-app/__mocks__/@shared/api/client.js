const client = {
  get: jest.fn(),
  post: jest.fn(),
  put: jest.fn(),
  patch: jest.fn(),
  delete: jest.fn(),
};

const setCsrfToken = jest.fn();
const setInMemoryToken = jest.fn();
const setRefreshCallback = jest.fn();
const getAuthHeader = jest.fn(() => Promise.resolve(null));

// Mirror the real contract closely enough for store/screen tests: backend
// detail wins, otherwise the caller's fallback.
const getApiErrorMessage = jest.fn((err, fallback = 'Something went wrong. Please try again.') => {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  return fallback;
});

module.exports = { __esModule: true, default: client, setCsrfToken, setInMemoryToken, setRefreshCallback, getAuthHeader, getApiErrorMessage };
