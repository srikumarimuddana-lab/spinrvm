/**
 * showErrorAlert — pins the body-text resolution order documented in the
 * file header: messageKey (i18n) > message > raw string > errors.unknown.
 */
import { Alert } from 'react-native';
import { showErrorAlert } from '../alert';

jest.mock('react-native', () => ({
  Alert: { alert: jest.fn() },
}));

jest.mock('../../i18n', () => ({
  // Minimal stand-in: for 'common.error' return the fallback ('Error');
  // for 'errors.unknown' return the fallback; for any other key, pretend
  // no translation exists so the caller's own fallback/message wins.
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
    showErrorAlert({ error: { message: 'Card declined' } });
    expect(Alert.alert).toHaveBeenCalledWith('Error', 'Card declined', undefined);
  });

  it('falls back to errors.unknown when the error carries nothing usable', () => {
    showErrorAlert({ error: {} });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Error',
      'Something went wrong. Please try again.',
      undefined,
    );
  });

  it('falls back to errors.unknown for a bare Error/undefined error', () => {
    showErrorAlert({});
    expect(Alert.alert).toHaveBeenCalledWith(
      'Error',
      'Something went wrong. Please try again.',
      undefined,
    );
  });

  it('appends an explicit actionHint after a blank line', () => {
    showErrorAlert({ error: 'Payment failed', actionHint: 'Try a different card.' });
    expect(Alert.alert).toHaveBeenCalledWith(
      'Error',
      'Payment failed\n\nTry a different card.',
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
    showErrorAlert({ title: 'Booking Failed', error: 'x' });
    expect(Alert.alert).toHaveBeenCalledWith('Booking Failed', 'x', undefined);
  });
});
