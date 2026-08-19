/**
 * toastConfig screen-reader wiring (ranked blocker #16, audit N5).
 *
 * toastConfig.tsx is the single error/status-announcement path for nearly
 * every form/failure in driver-app. Before this fix it rendered no
 * accessibility props at all, so a screen-reader user got no signal when a
 * toast appeared.
 *
 * Code under test: driver-app/components/toastConfig.tsx
 */
import React from 'react';
import { AccessibilityInfo } from 'react-native';
import { render } from '@testing-library/react-native';
import { toastConfig } from '../../components/toastConfig';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

beforeEach(() => {
  jest.restoreAllMocks();
  jest.spyOn(AccessibilityInfo, 'announceForAccessibility').mockImplementation(() => {});
});

describe('toastConfig — screen reader announcements', () => {
  it('announces an error toast and marks it assertive/alert', () => {
    const Render = toastConfig.error as any;
    const { getByRole } = render(
      <Render text1="Cannot Go Online" text2="Your license has expired." />,
    );

    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith(
      'Cannot Go Online. Your license has expired.',
    );
    const alert = getByRole('alert');
    expect(alert.props.accessibilityLiveRegion).toBe('assertive');
  });

  it('announces a non-error toast politely', () => {
    const Render = toastConfig.success as any;
    const { getByRole } = render(<Render text1="Ride Accepted" />);

    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith('Ride Accepted');
    const alert = getByRole('alert');
    expect(alert.props.accessibilityLiveRegion).toBe('polite');
  });

  it('only announces once per mounted toast instance', () => {
    const Render = toastConfig.warning as any;
    const { rerender } = render(<Render text1="Low Balance" text2="Top up soon." />);
    const callsAfterMount = (AccessibilityInfo.announceForAccessibility as jest.Mock).mock.calls
      .length;
    expect(callsAfterMount).toBeGreaterThan(0);

    // A re-render of the SAME mounted instance (e.g. a prop identity change
    // that isn't a new toast) must not re-announce.
    rerender(<Render text1="Low Balance" text2="Top up soon." />);
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledTimes(callsAfterMount);
  });
});
