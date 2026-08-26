/**
 * showErrorAlert (driver) — pins the body-text resolution order documented
 * in the file header: messageKey (i18n) > message > raw string >
 * errors.unknown. Identical contract to rider-app's lib/alert.ts.
 *
 * Code under test: driver-app/lib/alert.ts
 */
import { Alert } from 'react-native';
import { showErrorAlert } from '../../lib/alert';

jest.mock('react-native', () => ({
  Alert: { alert: jest.fn() },
}));

jest.mock('../../i18n', () => ({
  tKey: jest.fn((key: string, fallback?: string) => fallback ?? key),
}));

describe('showErrorAlert', () => {
  beforeEach(() => jest.clearAllMocks());

  it('uses the raw string when error is a string', () => {
    showErrorAlert({ error: 'Network unreachable' });
    expect(Alert.alert).toHaveBeenCalledWith('Error', 'Network unreachable', undefined);
  });

  it('prefers messageKey (i18n) over message when both are present', () => {
    const { tKey } = jest.requireMock('../../i18n');
    tKey.mockImplementation((key: string, fallback?: string) =>
      key === 'errors.rideTaken' ? 'This ride was just taken' : (fallback ?? key),
    );
    showErrorAlert({ error: { messageKey: 'errors.rideTaken', message: 'ride_taken' } });
    expect(Alert.alert).toHaveBeenCalledWith('Error', 'This ride was just taken', undefined);
  });

  it('falls back to the backend message when there is no messageKey', () => {
    showErrorAlert({ error: { message: 'Document upload failed' } });
    expect(Alert.alert).toHaveBeenCalledWith('Error', 'Document upload failed', undefined);
  });

  it('falls back to errors.unknown when the error carries nothing usable', () => {
    showErrorAlert({ error: {} });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Error',
      'Something went wrong. Please try again.',
      undefined,
    );
  });

  it('falls back to errors.unknown for a bare/undefined error', () => {
    showErrorAlert({});
    expect(Alert.alert).toHaveBeenCalledWith(
      'Error',
      'Something went wrong. Please try again.',
      undefined,
    );
  });

  it('appends an explicit actionHint after a blank line', () => {
    showErrorAlert({ error: 'Payout failed', actionHint: 'Check your bank details.' });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Error',
      'Payout failed\n\nCheck your bank details.',
      undefined,
    );
  });

  it('falls back to the structured actionHint when the caller supplies none', () => {
    showErrorAlert({ error: { message: 'Timed out', actionHint: 'Check your connection.' } });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Error',
      'Timed out\n\nCheck your connection.',
      undefined,
    );
  });

  it('passes custom buttons through unchanged', () => {
    const buttons = [{ text: 'Retry' }];
    showErrorAlert({ error: 'oops', buttons });
    expect(Alert.alert).toHaveBeenCalledWith('Error', 'oops', buttons);
  });

  it('respects a non-default title without triggering the common.error fallback', () => {
    showErrorAlert({ title: 'Document Rejected', error: 'x' });
    expect(Alert.alert).toHaveBeenCalledWith('Document Rejected', 'x', undefined);
  });
});
