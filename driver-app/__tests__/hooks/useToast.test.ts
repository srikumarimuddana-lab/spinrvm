/**
 * Toast length caps: showToast is the single entry point for driver-app
 * toasts, and toastConfig renders 1 title line + 2 message lines. Every
 * caller's content must be clamped here so no backend detail or native error
 * string can overflow the banner.
 *
 * Code under test: driver-app/hooks/useToast.ts::showToast
 */

import Toast from 'react-native-toast-message';
import { showToast } from '../../hooks/useToast';
import { setUnifiedToastEnabled, useUnifiedToastStore } from '../../store/unifiedToastStore';
import { TOAST_MESSAGE_MAX, TOAST_TITLE_MAX } from '@shared/utils/toastMessage';

jest.mock('react-native-toast-message', () => ({
  __esModule: true,
  default: { show: jest.fn() },
}));

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

// Migration 490: driver_unified_toast_enabled routes showToast to the unified
// toast store; off (the default) must be exactly the old Toast.show call.
describe('showToast — driver_unified_toast_enabled', () => {
  beforeEach(() => {
    useUnifiedToastStore.setState({ current: null });
    setUnifiedToastEnabled(false);
  });

  afterAll(() => {
    setUnifiedToastEnabled(false);
  });

  it('flag off: calls react-native-toast-message exactly as before and leaves the store empty', () => {
    showToast('error', 'Cannot Go Online', 'Your documents need review.');

    expect(Toast.show).toHaveBeenCalledTimes(1);
    expect(Toast.show).toHaveBeenCalledWith({
      type: 'error',
      text1: 'Cannot Go Online',
      text2: 'Your documents need review.',
      visibilityTime: 3500,
      topOffset: 60,
    });
    expect(useUnifiedToastStore.getState().current).toBeNull();
  });

  it('flag on: routes to the unified store with the same text and 3.5 s, not Toast.show', () => {
    setUnifiedToastEnabled(true);
    showToast('success', 'Ride Accepted', 'Head to the pickup.');

    expect(Toast.show).not.toHaveBeenCalled();
    expect(useUnifiedToastStore.getState().current).toMatchObject({
      title: 'Ride Accepted',
      message: 'Head to the pickup.',
      variant: 'success',
      duration: 3500,
    });
  });

  it('flag on: maps error to danger and keeps warning/info', () => {
    setUnifiedToastEnabled(true);
    showToast('error', 'Payment Failed');
    expect(useUnifiedToastStore.getState().current?.variant).toBe('danger');
    showToast('warning', 'Low Balance');
    expect(useUnifiedToastStore.getState().current?.variant).toBe('warning');
    showToast('info', 'Heads up');
    expect(useUnifiedToastStore.getState().current?.variant).toBe('info');
  });

  it('flag on: applies the same length caps', () => {
    setUnifiedToastEnabled(true);
    showToast('warning', 'x'.repeat(200), 'word '.repeat(60).trim());

    const current = useUnifiedToastStore.getState().current!;
    expect(current.title.length).toBeLessThanOrEqual(TOAST_TITLE_MAX);
    expect(current.message!.length).toBeLessThanOrEqual(TOAST_MESSAGE_MAX);
    expect(current.message!.endsWith('…')).toBe(true);
  });

  it('flag on: a repeat of the same toast is not de-duplicated', () => {
    setUnifiedToastEnabled(true);
    showToast('info', 'Ride Cancelled');
    const firstId = useUnifiedToastStore.getState().current!.id;
    showToast('info', 'Ride Cancelled');

    expect(useUnifiedToastStore.getState().current!.id).not.toBe(firstId);
  });

  it('turning the flag back off returns to react-native-toast-message', () => {
    setUnifiedToastEnabled(true);
    showToast('info', 'Unified');
    setUnifiedToastEnabled(false);
    showToast('info', 'Legacy');

    expect(Toast.show).toHaveBeenCalledTimes(1);
    expect(shown().text1).toBe('Legacy');
  });
});
