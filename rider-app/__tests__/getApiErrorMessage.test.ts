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

import { getApiErrorMessage, clampToastMessage, TOAST_MESSAGE_MAX } from '@shared/api/client';

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

const FALLBACK = 'Failed to save your profile. Please try again.';

// The live backend wording (backend/routes/users.py) — deliberately ≤140
// chars so it reaches the toast whole, with no clamping.
const DUPLICATE_EMAIL_DETAIL =
  'This email is already linked to an existing Spinr account. ' +
  "Please log in to that account, or contact support if you can't access it.";

// Representative over-long backend detail (the pre-2026-07 signup wording),
// kept as a fixture for the clamping tests.
const LONG_DETAIL =
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
    // The live wording is sized to reach the user whole — no ellipsis.
    expect(message).toBe(DUPLICATE_EMAIL_DETAIL);
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

  it('falls back on JSON-parse SyntaxErrors instead of leaking parser noise', () => {
    // Hermes wording (React Native)
    expect(getApiErrorMessage(new SyntaxError('JSON Parse error: Unexpected token <'), FALLBACK)).toBe(FALLBACK);
    // V8/web wording
    expect(
      getApiErrorMessage(new SyntaxError("Unexpected token '<', \"<html>\" is not valid JSON"), FALLBACK),
    ).toBe(FALLBACK);
    // Same wording arriving as a plain Error (re-wrapped upstream) is still noise
    expect(getApiErrorMessage(new Error('JSON Parse error: Unexpected character: o'), FALLBACK)).toBe(FALLBACK);
  });

  it('falls back when the response body carries no recognizable detail', () => {
    expect(getApiErrorMessage({ response: { status: 500, data: {} } }, FALLBACK)).toBe(FALLBACK);
  });

  // Live-testing report: the booking screen rendered "Booking Failed —
  // undefined is not a function". That is Hermes crash text, not a backend
  // rejection: a client-side throw inside the booking handler lands in the
  // same catch as an API error, and its `.message` was passed straight to the
  // toast. The rider saw engine gibberish where the real reason belonged.
  describe('engine-generated crashes never become toast copy', () => {
    const BOOKING_FALLBACK = 'Failed to book ride. Please try again.';

    it.each([
      // Hermes / JSC (React Native)
      'undefined is not a function',
      "undefined is not an object (evaluating 'ride.id')",
      "null is not an object (evaluating 'estimate.total_fare')",
      "confirmPayment.call is not a function (it is undefined)",
      // V8 / web (admin dashboard shares this client)
      "Cannot read properties of undefined (reading 'total_fare')",
      "Cannot read property 'id' of null",
      'estimates.find is not a function',
      'routePolyline is not iterable',
    ])('falls back instead of leaking %p', (message) => {
      expect(getApiErrorMessage(new TypeError(message), BOOKING_FALLBACK)).toBe(BOOKING_FALLBACK);
    });

    it('filters engine crashes that arrive with the name stripped', () => {
      // Rethrown as a plain object across a store/bridge boundary: `message`
      // survives, `name` does not.
      expect(getApiErrorMessage({ message: 'undefined is not a function' }, BOOKING_FALLBACK)).toBe(
        BOOKING_FALLBACK,
      );
    });

    it('filters ReferenceError and RangeError by name', () => {
      expect(getApiErrorMessage(new ReferenceError('Analytics is not defined'), BOOKING_FALLBACK)).toBe(
        BOOKING_FALLBACK,
      );
      expect(getApiErrorMessage(new RangeError('Maximum call stack size exceeded'), BOOKING_FALLBACK)).toBe(
        BOOKING_FALLBACK,
      );
    });

    it('still prefers the backend body when a crash-shaped error also carries one', () => {
      // Defence in depth: the response body is checked before `.message`, so a
      // real 402 keeps its reason even if the thrown object looks engine-ish.
      const err = Object.assign(new TypeError('undefined is not a function'), {
        response: { status: 402, data: { detail: 'Your card was declined.' } },
      });
      expect(getApiErrorMessage(err, BOOKING_FALLBACK)).toBe('Your card was declined.');
    });

    it('leaves deliberately thrown domain messages alone', () => {
      // rideStore.createRide throws these as real rider-facing copy — they are
      // plain Errors, not engine crashes, and must survive the filter.
      expect(getApiErrorMessage(new Error('A ride is already active'), BOOKING_FALLBACK)).toBe(
        'A ride is already active',
      );
      expect(
        getApiErrorMessage(
          new Error('Your destination looks too close to your pickup. Please re-select it so we price the trip correctly.'),
          BOOKING_FALLBACK,
        ),
      ).toContain('too close to your pickup');
    });
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
    const clamped = clampToastMessage(LONG_DETAIL);
    expect(clamped.length).toBeLessThanOrEqual(TOAST_MESSAGE_MAX);
    expect(clamped.endsWith('…')).toBe(true);
    expect(clamped).not.toMatch(/\s…$/); // no dangling space before the ellipsis
  });

  it('leaves short messages untouched', () => {
    expect(clampToastMessage('Phone number already in use')).toBe('Phone number already in use');
  });
});
