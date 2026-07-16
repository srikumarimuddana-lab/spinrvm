/**
 * Toast length caps: showToast is the single entry point for driver-app
 * toasts, and toastConfig renders 1 title line + 2 message lines. Every
 * caller's content must be clamped here so no backend detail or native error
 * string can overflow the banner.
 *
 * Code under test: driver-app/hooks/useToast.ts::showToast
 */

jest.mock('react-native-toast-message', () => ({
  __esModule: true,
  default: { show: jest.fn() },
}));

import Toast from 'react-native-toast-message';
import { showToast } from '../../hooks/useToast';
import { TOAST_MESSAGE_MAX, TOAST_TITLE_MAX } from '@shared/utils/toastMessage';

const shown = () => (Toast.show as jest.Mock).mock.calls.at(-1)![0];

beforeEach(() => {
  (Toast.show as jest.Mock).mockClear();
});

describe('showToast — length caps', () => {
  it('clamps an over-long message to TOAST_MESSAGE_MAX at a word boundary', () => {
    const longMessage = 'word '.repeat(60).trim(); // 299 chars
    showToast('error', 'Cannot Go Online', longMessage);
    expect(shown().text2.length).toBeLessThanOrEqual(TOAST_MESSAGE_MAX);
    expect(shown().text2.endsWith('…')).toBe(true);
  });

  it('clamps an over-long title to TOAST_TITLE_MAX', () => {
    const longTitle = 'This title is far too long to be a title and reads like a message body instead';
    showToast('warning', longTitle, 'Short message.');
    expect(shown().text1.length).toBeLessThanOrEqual(TOAST_TITLE_MAX);
    expect(shown().text1.endsWith('…')).toBe(true);
  });

  it('leaves short content untouched and keeps undefined message undefined', () => {
    showToast('success', 'Ride Accepted');
    expect(shown().text1).toBe('Ride Accepted');
    expect(shown().text2).toBeUndefined();
  });
});
