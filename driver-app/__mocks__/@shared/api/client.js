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

const { messageForSentinel } = require('../../errors/sentinelMessages');
const { clampToastMessage } = require('../../utils/toastMessage');

// Mirror the real contract for the parts driver-app tests actually assert on.
// This used to be "return detail verbatim", which meant no driver-app test
// could ever catch a regression in the sentinel mapping or the clamp — a
// driver test feeding {detail: 'ERR_SESSION_REVOKED'} asserted the raw token
// as expected copy and passed, while production showed the mapped sentence.
// The two helpers below are pure and dependency-free, so using the real ones
// keeps this stub honest without dragging fetch/SecureStore into the sandbox.
const MACHINE_ERROR_SENTINEL = /^(?:ERR|ERROR)_[A-Z0-9_]+$/;

const getApiErrorMessage = jest.fn((err, fallback = 'Something went wrong. Please try again.') => {
  const detail = err?.response?.data?.detail;
  const raw = typeof detail === 'string' && detail ? detail : err?.message;

  const mapped = messageForSentinel(raw);
  if (mapped) return clampToastMessage(mapped);

  if (typeof raw === 'string' && raw && !MACHINE_ERROR_SENTINEL.test(raw.trim())) {
    return clampToastMessage(raw);
  }
  return fallback;
});

module.exports = { __esModule: true, default: client, setCsrfToken, setInMemoryToken, setRefreshCallback, getAuthHeader, getApiErrorMessage };
