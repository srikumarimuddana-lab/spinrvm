/**
 * notifyError glue (rider): composes getApiErrorMessage (body) + presentError
 * (title/severity) and renders through the toast store, mapping the canonical
 * 'error' severity to the store's 'danger' variant.
 *
 * Code under test: rider-app/lib/notifyError.ts
 */

import { notifyError } from '../lib/notifyError';
import { showToast } from '../store/toastStore';

jest.mock('../store/toastStore', () => ({
  __esModule: true,
  showToast: jest.fn(),
}));

const showToastMock = showToast as jest.Mock;

// A duplicate-email error as the client would surface it.
const duplicateEmailError = {
  name: 'SpinrApiError',
  code: 3002,
  details: { field: 'email' },
  message: 'This email is already linked to an existing Spinr account. Please log in to that account, or contact support if you can’t access it.',
  response: {
    status: 400,
    data: {
      error: {
        code: 3002,
        details: { field: 'email' },
        message: 'This email is already linked to an existing Spinr account. Please log in to that account, or contact support if you can’t access it.',
      },
    },
  },
};

beforeEach(() => showToastMock.mockClear());

describe('notifyError (rider)', () => {
  it('renders the field-specific title, backend body, and danger variant for a duplicate email', () => {
    notifyError(duplicateEmailError, { fallbackTitle: 'Profile Not Saved' });
    expect(showToastMock).toHaveBeenCalledTimes(1);
    const [title, message, variant] = showToastMock.mock.calls[0];
    expect(title).toBe('Email Already In Use');
    expect(message).toContain('already linked to an existing Spinr account');
    expect(variant).toBe('danger'); // canonical 'error' → rider 'danger'
  });

  it('uses the caller fallback title + message when the error is unstructured', () => {
    notifyError(new Error('Network Error'), {
      fallbackTitle: 'Profile Not Saved',
      fallbackMessage: 'Failed to save your profile. Please try again.',
    });
    const [title, message, variant] = showToastMock.mock.calls[0];
    expect(title).toBe('Profile Not Saved');
    expect(message).toBe('Failed to save your profile. Please try again.');
    expect(variant).toBe('danger');
  });

  it('maps a rate-limit error to the warning variant with a retry-aware title', () => {
    notifyError(
      { name: 'RateLimitError', status: 429, message: 'Too many attempts. Try again in 60s.', retryAfterSeconds: 60 },
      { fallbackTitle: 'Sign-in Failed' },
    );
    const [title, , variant] = showToastMock.mock.calls[0];
    expect(title).toBe('Too Many Attempts');
    expect(variant).toBe('warning');
  });
});
