/**
 * R-P2-36: FreeCancelTimer component tests
 *
 * Pins FreeCancelTimer (UX-001):
 *   - Rendering: open window, countdown from acceptedAt, expired window,
 *     custom fee, compact mode (open + expired)
 *   - Behaviour: onExpire callback, countdown decrements with fake timers
 *   - Accessibility: accessibilityLabel on compact open, accessibilityLiveRegion
 *     switches to "assertive" once expired
 *
 * Code under test:
 *   rider-app/components/FreeCancelTimer.tsx
 */

jest.mock('@expo/vector-icons', () => ({
  Ionicons: 'Ionicons',
}));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: { theme: { colors: { primary: '#FF3B30' } } },
}));

import React from 'react';
import { render, act, configure } from '@testing-library/react-native';
import { FreeCancelTimer } from '../FreeCancelTimer';

// Pre-configure RTLN host component names to bypass the Switch/AndroidSwitch
// native-component detection that fails under jest-expo's test environment.
configure({
  hostComponentNames: {
    text: 'Text',
    textInput: 'TextInput',
    image: 'Image',
    switch: 'Switch',
    scrollView: 'ScrollView',
    modal: 'Modal',
    view: 'View',
    picker: 'Picker',
    touchableHighlight: 'TouchableHighlight',
    touchableOpacity: 'TouchableOpacity',
    touchableNativeFeedback: 'TouchableNativeFeedback',
    touchableWithoutFeedback: 'TouchableWithoutFeedback',
    // RTLN 12 only requires a subset; extra keys are ignored.
  } as Parameters<typeof configure>[0]['hostComponentNames'],
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Returns an ISO timestamp representing a point `secondsAgo` seconds before now. */
const acceptedSecondsAgo = (secondsAgo: number): string =>
  new Date(Date.now() - secondsAgo * 1000).toISOString();

// ---------------------------------------------------------------------------
// Suite
// ---------------------------------------------------------------------------

describe('FreeCancelTimer', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  // -------------------------------------------------------------------------
  // Rendering — open window
  // -------------------------------------------------------------------------

  it('shows fee state when driverAcceptedAt is null (isWindowOpen requires truthy timestamp)', () => {
    // isWindowOpen = secondsLeft > 0 && !!driverAcceptedAt.
    // When driverAcceptedAt is null, !!null === false, so the fee view is shown
    // regardless of secondsLeft. The timer interval also does not start.
    const { getByText } = render(
      <FreeCancelTimer driverAcceptedAt={null} freeCancelWindowSeconds={120} />,
    );

    expect(getByText('Cancellation fee applies')).toBeTruthy();
    expect(getByText('$3.00')).toBeTruthy();
  });

  it('shows "Free cancellation" and full countdown when driverAcceptedAt is now', () => {
    const now = Date.now();
    jest.setSystemTime(now);

    // Accepted right now → 120 s remaining → 2:00
    const { getByText } = render(
      <FreeCancelTimer
        driverAcceptedAt={new Date(now).toISOString()}
        freeCancelWindowSeconds={120}
      />,
    );

    expect(getByText('Free cancellation')).toBeTruthy();
    expect(getByText('2:00 remaining')).toBeTruthy();
  });

  it('shows remaining countdown when driverAcceptedAt was 30 s ago (120 s window)', () => {
    const now = Date.now();
    jest.setSystemTime(now);

    const { getByText } = render(
      <FreeCancelTimer
        driverAcceptedAt={new Date(now - 30_000).toISOString()}
        freeCancelWindowSeconds={120}
      />,
    );

    // 120 - 30 = 90 s → 1:30
    expect(getByText('1:30 remaining')).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // Rendering — expired window
  // -------------------------------------------------------------------------

  it('shows "Cancellation fee applies" when driverAcceptedAt is beyond the window', () => {
    const now = Date.now();
    jest.setSystemTime(now);

    const { getByText } = render(
      <FreeCancelTimer
        driverAcceptedAt={new Date(now - 200_000).toISOString()}
        freeCancelWindowSeconds={120}
      />,
    );

    expect(getByText('Cancellation fee applies')).toBeTruthy();
    expect(getByText('$3.00')).toBeTruthy();
  });

  it('shows custom cancellationFee when the window is expired', () => {
    const now = Date.now();
    jest.setSystemTime(now);

    const { getByText } = render(
      <FreeCancelTimer
        driverAcceptedAt={new Date(now - 200_000).toISOString()}
        freeCancelWindowSeconds={120}
        cancellationFee={5.0}
      />,
    );

    expect(getByText('$5.00')).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // Rendering — compact mode
  // -------------------------------------------------------------------------

  it('compact + open window → renders single-line "Free cancel — ... left" text', () => {
    const now = Date.now();
    jest.setSystemTime(now);

    const { getByText } = render(
      <FreeCancelTimer
        driverAcceptedAt={new Date(now - 10_000).toISOString()}
        freeCancelWindowSeconds={120}
        compact
      />,
    );

    // 120 - 10 = 110 s → 1:50
    expect(getByText('Free cancel — 1:50 left')).toBeTruthy();
  });

  it('compact + expired window → renders single-line "Cancel fee: $3.00" text', () => {
    const now = Date.now();
    jest.setSystemTime(now);

    const { getByText } = render(
      <FreeCancelTimer
        driverAcceptedAt={new Date(now - 200_000).toISOString()}
        freeCancelWindowSeconds={120}
        compact
      />,
    );

    expect(getByText('Cancel fee: $3.00')).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // Behaviour — onExpire callback
  // -------------------------------------------------------------------------

  it('calls onExpire exactly once when the countdown reaches zero', () => {
    const mockExpire = jest.fn();
    const now = Date.now();
    jest.setSystemTime(now);

    // Window = 5 s, accepted 4 s ago → 1 s left.
    const accepted = new Date(now - 4_000).toISOString();

    render(
      <FreeCancelTimer
        driverAcceptedAt={accepted}
        freeCancelWindowSeconds={5}
        onExpire={mockExpire}
      />,
    );

    act(() => {
      // advanceTimersByTime(ms) fires intervals AND advances Date.now() by ms.
      // Do NOT also call setSystemTime — that would double-advance the clock.
      jest.advanceTimersByTime(2_000);
    });

    expect(mockExpire).toHaveBeenCalledTimes(1);
  });

  // -------------------------------------------------------------------------
  // Behaviour — countdown decrements
  // -------------------------------------------------------------------------

  it('countdown decrements by 1 s after 1000 ms', () => {
    // Pin the clock to a fixed epoch before creating the accepted timestamp so
    // that Date.now() inside the component's interval and the test's now are
    // identical (prevents sub-second drift producing elapsed = 2 on first tick).
    const now = 1_700_000_000_000; // fixed epoch ms
    jest.setSystemTime(now);

    // 10 s window, accepted right now → shows 0:10
    const accepted = new Date(now).toISOString();

    const { getByText } = render(
      <FreeCancelTimer
        driverAcceptedAt={accepted}
        freeCancelWindowSeconds={10}
      />,
    );

    expect(getByText('0:10 remaining')).toBeTruthy();

    act(() => {
      // advanceTimersByTime fires the interval AND advances Date.now() by 1000ms.
      // Do NOT also call setSystemTime — that would double-advance the clock.
      jest.advanceTimersByTime(1_000);
    });

    expect(getByText('0:09 remaining')).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // Accessibility — compact open window
  // -------------------------------------------------------------------------

  it('compact + open window → accessibilityLabel describes the countdown', () => {
    const now = Date.now();
    jest.setSystemTime(now);

    const { getByLabelText } = render(
      <FreeCancelTimer
        driverAcceptedAt={new Date(now - 10_000).toISOString()}
        freeCancelWindowSeconds={120}
        compact
      />,
    );

    // Label pattern: "Free cancellation — 1:50 remaining"
    expect(
      getByLabelText('Free cancellation — 1:50 remaining'),
    ).toBeTruthy();
  });

  // -------------------------------------------------------------------------
  // Accessibility — compact expired window
  // -------------------------------------------------------------------------

  it('compact + expired window → accessibilityLiveRegion is "assertive"', () => {
    const now = Date.now();
    jest.setSystemTime(now);

    const { UNSAFE_getByProps } = render(
      <FreeCancelTimer
        driverAcceptedAt={new Date(now - 200_000).toISOString()}
        freeCancelWindowSeconds={120}
        compact
      />,
    );

    // The Text element in compact mode carries accessibilityLiveRegion="assertive"
    // when secondsLeft === 0.
    const el = UNSAFE_getByProps({ accessibilityLiveRegion: 'assertive' });
    expect(el).toBeTruthy();
  });
});
