/**
 * Toast screen-reader wiring (ranked blocker #16, audit N5).
 *
 * Toast.tsx is the single error/status-announcement path for nearly every
 * form/failure in rider-app. Before this fix it rendered no accessibility
 * props at all, so a screen-reader user got no signal when a toast appeared.
 *
 * Code under test: rider-app/components/Toast.tsx
 */
import React from 'react';
import { AccessibilityInfo } from 'react-native';
import { render, act } from '@testing-library/react-native';
import Toast from '../Toast';
import { useToastStore, showToast } from '../../store/toastStore';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

beforeEach(() => {
  useToastStore.setState({ current: null });
  jest.restoreAllMocks();
  jest.spyOn(AccessibilityInfo, 'announceForAccessibility').mockImplementation(() => {});
});

describe('Toast — screen reader announcements', () => {
  it('announces a danger toast and marks it assertive/alert', () => {
    const { getByRole } = render(<Toast />);

    act(() => {
      showToast('Payment Failed', 'Your card was declined.', 'danger');
    });

    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith(
      'Payment Failed. Your card was declined.',
    );
    const alert = getByRole('alert');
    expect(alert.props.accessibilityLiveRegion).toBe('assertive');
  });

  it('announces a non-danger toast politely', () => {
    const { getByRole } = render(<Toast />);

    act(() => {
      showToast('Ride Booked', undefined, 'success');
    });

    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith('Ride Booked');
    const alert = getByRole('alert');
    expect(alert.props.accessibilityLiveRegion).toBe('polite');
  });

  it('does not re-announce a deduped repeat of the same toast', () => {
    render(<Toast />);

    act(() => {
      showToast('Ride Cancelled', 'Your ride has been cancelled.', 'warning');
    });
    const callsAfterFirstShow = (AccessibilityInfo.announceForAccessibility as jest.Mock).mock
      .calls.length;
    expect(callsAfterFirstShow).toBeGreaterThan(0);

    act(() => {
      showToast('Ride Cancelled', 'Your ride has been cancelled.', 'warning');
    });
    // Same content within the dedupe window keeps the same id — no re-announce.
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledTimes(callsAfterFirstShow);
  });
});
