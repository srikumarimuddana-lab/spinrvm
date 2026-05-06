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

module.exports = { __esModule: true, default: client, setCsrfToken, setInMemoryToken, setRefreshCallback, getAuthHeader };
