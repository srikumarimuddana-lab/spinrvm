/**
 * notifyError glue (driver): composes getApiErrorMessage (body) + presentError
 * (title/severity) and renders through the toast hook. Driver toast types
 * match the canonical severity names, so severity maps identically.
 *
 * Code under test: driver-app/lib/notifyError.ts
 */

jest.mock('../../hooks/useToast', () => ({
  __esModule: true,
  showToast: jest.fn(),
}));

import { notifyError } from '../../lib/notifyError';
import { showToast } from '../../hooks/useToast';

const showToastMock = showToast as jest.Mock;

// A duplicate-email error as the client surfaces it (structured body).
const duplicateEmailError = {
  name: 'SpinrApiError',
  code: 3002,
  details: { field: 'email' },
  response: {
    status: 400,
    data: {
      detail: 'This email is already linked to an existing Spinr account. Please log in to that account, or contact support if you can’t access it.',
      error: {
        code: 3002,
        details: { field: 'email' },
      },
    },
  },
};

beforeEach(() => showToastMock.mockClear());

describe('notifyError (driver)', () => {
  it('renders type=error, field-specific title, and backend body for a duplicate email', () => {
    notifyError(duplicateEmailError, { fallbackTitle: 'Profile Not Saved' });
    expect(showToastMock).toHaveBeenCalledTimes(1);
    const [type, title, message] = showToastMock.mock.calls[0];
    expect(type).toBe('error');
    expect(title).toBe('Email Already In Use');
    expect(message).toContain('already linked to an existing Spinr account');
  });

  it('uses the caller fallback title + message for an unstructured error', () => {
    notifyError(new Error('Network Error'), {
      fallbackTitle: 'Profile Not Saved',
      fallbackMessage: 'Failed to create profile. Please try again.',
    });
    const [type, title, message] = showToastMock.mock.calls[0];
    expect(type).toBe('error');
    expect(title).toBe('Profile Not Saved');
    expect(message).toBe('Failed to create profile. Please try again.');
  });

  it('maps a go-online quota error to an info-type "Daily Limit Reached" toast', () => {
    notifyError(
      { response: { status: 403, data: { error: { code: 5006 } } } },
      { fallbackTitle: 'Cannot Go Online' },
    );
    const [type, title] = showToastMock.mock.calls[0];
    expect(type).toBe('info');
    expect(title).toBe('Daily Limit Reached');
  });
});
