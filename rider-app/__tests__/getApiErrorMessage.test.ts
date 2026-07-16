/**
 * Regression: error toasts must surface the backend's specific reason, not a
 * blanket "try again".
 *
 * The signup screen used to show "Profile Not Saved — Failed to save your
 * profile. Please try again." even when POST /users/profile rejected with
 * 400 "This email is already linked to an existing Spinr account…". The user
 * had no way to know the fix was changing the email, not retrying.
 *
 * Code under test: shared/api/client.ts::getApiErrorMessage / clampToastMessage
 */

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { backendUrl: 'http://localhost:8000' },
}));

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(() => Promise.resolve(null)),
  setItemAsync: jest.fn(() => Promise.resolve()),
  deleteItemAsync: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/store/authStore', () => ({
  useAuthStore: {
    getState: jest.fn(() => ({ token: null, logout: jest.fn() })),
  },
}));

import { getApiErrorMessage, clampToastMessage, TOAST_MESSAGE_MAX } from '@shared/api/client';

const FALLBACK = 'Failed to save your profile. Please try again.';

const DUPLICATE_EMAIL_DETAIL =
  'This email is already linked to an existing Spinr account. Your rider ' +
  'and driver profiles share one account — please log in to that account ' +
  'instead of creating a new one. If you no longer have access to its ' +
  'phone number, contact support.';

describe('getApiErrorMessage', () => {
  it('surfaces the backend detail for a duplicate-email 400 instead of the generic fallback', () => {
    const err = {
      response: { status: 400, data: { detail: DUPLICATE_EMAIL_DETAIL } },
      message: 'Request failed with status code 400',
    };
    const message = getApiErrorMessage(err, FALLBACK);
    expect(message).toContain('This email is already linked to an existing Spinr account');
    expect(message).not.toBe(FALLBACK);
    // Must fit the two-line toast box.
    expect(message.length).toBeLessThanOrEqual(TOAST_MESSAGE_MAX);
  });

  it('uses a meaningful re-thrown err.message when there is no response body (authStore.createProfile path)', () => {
    expect(getApiErrorMessage(new Error(DUPLICATE_EMAIL_DETAIL), FALLBACK)).toContain(
      'This email is already linked',
    );
  });

  it('falls back on Axios generic messages and network errors', () => {
    expect(getApiErrorMessage(new Error('Request failed with status code 400'), FALLBACK)).toBe(FALLBACK);
    expect(getApiErrorMessage(new Error('Network Error'), FALLBACK)).toBe(FALLBACK);
    expect(getApiErrorMessage(new Error('timeout of 15000ms exceeded'), FALLBACK)).toBe(FALLBACK);
    expect(getApiErrorMessage(null, FALLBACK)).toBe(FALLBACK);
    expect(getApiErrorMessage(undefined, FALLBACK)).toBe(FALLBACK);
  });

  it('falls back when the response body carries no recognizable detail', () => {
    expect(getApiErrorMessage({ response: { status: 500, data: {} } }, FALLBACK)).toBe(FALLBACK);
  });

  it('never leaks the bare "Request failed" extractor sentinel', () => {
    expect(getApiErrorMessage(new Error('Request failed'), FALLBACK)).toBe(FALLBACK);
  });

  // 429s throw RateLimitError, which has NO `.response` — before this branch
  // existed, login screens reading `err.response?.data?.detail` showed
  // "Connection Error — Unable to reach server" for a lockout.
  describe('RateLimitError (429)', () => {
    it('prefers the backend lockout message when present', () => {
      const err = {
        name: 'RateLimitError',
        message: 'Too many attempts. Locked for 24 hours.',
        retryAfterSeconds: 86400,
      };
      expect(getApiErrorMessage(err, FALLBACK)).toBe('Too many attempts. Locked for 24 hours.');
    });

    it('synthesizes a retry hint from Retry-After when the body had no detail', () => {
      const err = { name: 'RateLimitError', message: 'Request failed', retryAfterSeconds: 60 };
      expect(getApiErrorMessage(err, FALLBACK)).toBe('Too many requests — please try again in 60s.');
    });

    it('still says "too many requests" (not the fallback) with no retry information', () => {
      const err = { name: 'RateLimitError', message: 'Request failed' };
      expect(getApiErrorMessage(err, FALLBACK)).toBe('Too many requests — please try again shortly.');
    });
  });

  it('joins FastAPI validation-error arrays into one readable message', () => {
    const err = {
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' },
          ],
        },
      },
    };
    expect(getApiErrorMessage(err, FALLBACK)).toContain('valid email address');
  });
});

describe('clampToastMessage', () => {
  it('clamps long backend messages at a word boundary with an ellipsis', () => {
    const clamped = clampToastMessage(DUPLICATE_EMAIL_DETAIL);
    expect(clamped.length).toBeLessThanOrEqual(TOAST_MESSAGE_MAX);
    expect(clamped.endsWith('…')).toBe(true);
    expect(clamped).not.toMatch(/\s…$/); // no dangling space before the ellipsis
  });

  it('leaves short messages untouched', () => {
    expect(clampToastMessage('Phone number already in use')).toBe('Phone number already in use');
  });
});
