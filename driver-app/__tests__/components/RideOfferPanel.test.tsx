import React from 'react';
import { act, render, fireEvent } from '@testing-library/react-native';
import { ScrollView, StyleSheet } from 'react-native';
import { RideOfferPanel, DECLINE_REASON_SERVICE_ANIMAL } from '../../components/panels/RideOfferPanel';

jest.mock('../../components/AlertDialog', () => ({
  showAlert: jest.fn(),
}));

jest.mock('@shared/config/spinr.config', () => ({
  __esModule: true,
  default: {
    theme: {
      colors: {
        primary: '#FF3B30',
        primaryDark: '#D32F2F',
        background: '#FFFFFF',
        surface: '#FFFFFF',
        surfaceLight: '#F5F5F5',
        text: '#1A1A1A',
        textDim: '#666666',
        border: '#E5E7EB',
        accent: '#FF3B30',
        accentDim: '#D32F2F',
        danger: '#DC2626',
        gold: '#FFD700',
      },
    },
    rideOffer: { countdownSeconds: 15 },
  },
}));

jest.mock('expo-linear-gradient', () => {
  const { View } = require('react-native');
  return { LinearGradient: ({ children, ...props }: any) => <View {...props}>{children}</View> };
});

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

// expo-image is a real native module — importing it pulls in Expo's Winter
// runtime (import.meta polyfill install), which crashes outside a real
// device/simulator context. Stub with a plain, inert View (same convention
// as the LinearGradient mock above) rather than react-native's real Image,
// which schedules Animated timers that outlive the test and crash on
// teardown.
jest.mock('expo-image', () => {
  const { View } = require('react-native');
  return { Image: (props: any) => <View {...props} /> };
});

const mockRide = {
  ride_id: 'ride-001',
  pickup_address: '123 Main St',
  dropoff_address: '456 Elm Ave',
  pickup_lat: 52.1333,
  pickup_lng: -106.6667,
  dropoff_lat: 52.15,
  dropoff_lng: -106.65,
  fare: 15.5,
  distance_km: 2.5,
  duration_minutes: 10,
  rider_name: 'Jane D.',
  rider_rating: 4.8,
};

const defaultProps = {
  incomingRide: mockRide,
  countdownSeconds: 12,
  isLoading: false,
  onAccept: jest.fn(),
  onDecline: jest.fn(),
};

describe('RideOfferPanel', () => {
  // The component starts Animated.spring/timing loops on mount with no
  // cleanup on unmount (pre-existing, not this test's concern to fix).
  // Fake timers let Jest own and safely discard those timers at teardown
  // instead of a real setTimeout firing after the environment is torn down.
  beforeEach(() => {
    jest.useFakeTimers();
  });

  // ACTION_ITEMS.md C37: @testing-library/react-native's own afterEach(cleanup)
  // is registered at import time (top of this file), so it runs -- and
  // unmounts the tree -- BEFORE this afterEach. Flushing pending timers here
  // then fires the component's still-pending Animated callback against an
  // already-unmounted renderer, scheduling a React state update outside any
  // act() (the "update ... was not wrapped in act(...)" warning). That leaked,
  // unguarded update isn't fully discharged before this test file's teardown
  // returns, and can bleed into whichever test file the Jest worker runs next
  // -- this was root-caused as the cause of the intermittent
  // ActivityView.test.tsx timeout (same failure class as C31's leaked-renderer
  // fix, different trigger). Wrapping the flush in act() forces React to
  // process and fully discharge the update synchronously, right here.
  afterEach(() => {
    act(() => {
      jest.runOnlyPendingTimers();
    });
    jest.useRealTimers();
  });

  it('renders without crashing', () => {
    const { toJSON } = render(<RideOfferPanel {...defaultProps} />);
    expect(toJSON()).not.toBeNull();
  });

  it('renders Accept and Decline buttons', () => {
    const { getByText } = render(<RideOfferPanel {...defaultProps} />);
    expect(getByText('Accept')).toBeTruthy();
    expect(getByText('Decline')).toBeTruthy();
  });

  it('shows fare amount', () => {
    const { getByText } = render(<RideOfferPanel {...defaultProps} />);
    expect(getByText('$15.50')).toBeTruthy();
  });

  it('shows ETA when duration_minutes is provided', () => {
    // Rendered as two sibling <Text> nodes ("10" and "min"), not one
    // combined string — match them separately.
    const { getByText } = render(<RideOfferPanel {...defaultProps} />);
    expect(getByText('10')).toBeTruthy();
    expect(getByText('min')).toBeTruthy();
  });

  it('shows countdown seconds', () => {
    const { getByText } = render(<RideOfferPanel {...defaultProps} countdownSeconds={9} />);
    expect(getByText('9')).toBeTruthy();
  });

  it('renders nothing when incomingRide is null', () => {
    const { toJSON } = render(<RideOfferPanel {...defaultProps} incomingRide={null} />);
    expect(toJSON()).toBeNull();
  });

  // Design-audit finding: an offer with several optional sections stacked
  // (badges + incentive + quest + long rider name/address) had no
  // scroll/height safeguard and could push content off the top of the
  // screen. The fix wraps the informational content in a bounded
  // ScrollView while keeping the timer and action bar outside it. This
  // test can only prove Accept/Decline stay in the render tree and enabled
  // under that worst-case stack -- it does NOT prove they are visually
  // on-screen on a real device (no simulator/device is available here).
  it('keeps Accept and Decline present and enabled under a worst-case content stack (all badges + incentive + quest + long name/address)', () => {
    const worstCaseRide = {
      ...mockRide,
      rider_name: 'Alexandria Something-Very-Long Montgomery-Whitfield III',
      pickup_address:
        '1234 Extremely Long Boulevard Avenue Northwest, Building C, Suite 4500, Saskatoon, Saskatchewan',
      dropoff_address:
        '9876 Another Ridiculously Long Crescent Drive, Unit 12B, Regina, Saskatchewan',
      surge_multiplier: 2.0,
      requires_wav: true,
      quiet_mode: true,
      payment_method: 'cash',
      is_scheduled: true,
      total_bonus: 5,
      incentives: [{ name: 'Peak hour bonus', bonus_amount: 3.5, incentive_type: 'peak' }],
      quest_hint: {
        title: 'Weekend Warrior',
        current_value: 7,
        target_value: 10,
        progress_pct: 70,
        reward_amount: 25,
      },
    };
    const { getByLabelText, getByText } = render(
      <RideOfferPanel {...defaultProps} incomingRide={worstCaseRide} />,
    );

    // Confirm the stack really is worst-case (every optional section
    // actually rendered), not silently skipped.
    expect(getByText('Pre-booked')).toBeTruthy();
    expect(getByText('2.0x Surge')).toBeTruthy();
    expect(getByText('WAV')).toBeTruthy();
    expect(getByText('Quiet ride')).toBeTruthy();
    expect(getByText('Cash')).toBeTruthy();
    expect(getByText('Peak hour bonus')).toBeTruthy();
    expect(getByText('Weekend Warrior — 7/10')).toBeTruthy();

    const acceptBtn = getByLabelText('Accept ride');
    const declineBtn = getByLabelText('Decline ride');
    expect(acceptBtn).toBeTruthy();
    expect(declineBtn).toBeTruthy();
    expect(acceptBtn.props.disabled).toBeFalsy();
    expect(declineBtn.props.disabled).toBeFalsy();
  });

  // Regression, #5324 follow-up: the scroll-safety fix above sized that
  // ScrollView with `flex: 1`. The card around it is auto-height with only a
  // maxHeight cap and no flexGrow of its own, so there is never free space to
  // grow back into — `flex: 1`'s flexBasis 0 measured the body as zero and
  // drivers got an offer card with the header and the Accept/Decline buttons
  // and nothing in between: no earnings, no km/min, no pickup or drop-off
  // address.
  //
  // jest does no layout, so this asserts the style *shape* rather than a
  // measured height. It deliberately pins the two ingredients that decide
  // behaviour, not the exact spelling of the fix: RN's own ScrollView base
  // style already supplies flexGrow/flexShrink with flexBasis left at `auto`,
  // so passing no style prop at all (ActiveRidePanel.tsx's idiom) is equally
  // correct and must not redden this test.
  it('never sizes the informational body with a flexBasis of 0, and leaves it shrinkable', () => {
    const { UNSAFE_getByType } = render(<RideOfferPanel {...defaultProps} />);

    const body = StyleSheet.flatten(UNSAFE_getByType(ScrollView).props.style) ?? {};

    // A positive `flex: N` shorthand expands to flexBasis 0 — the collapse.
    expect(typeof body.flex === 'number' && body.flex > 0).toBe(false);
    // ...as does setting flexBasis to zero directly, in either spelling.
    expect(body.flexBasis ?? 'auto').not.toBe(0);
    expect(body.flexBasis ?? 'auto').not.toBe('0%');
    // The other half of #5324's contract: the body must stay shrinkable, so a
    // worst-case stack is absorbed here instead of pushing the action bar past
    // the card's `overflow: hidden` clip. Undefined inherits RN's own
    // flexShrink: 1, so only an explicit 0 breaks it.
    expect(body.flexShrink ?? 1).not.toBe(0);
  });

  // Companion to the style assertion above: the sections that vanished in
  // #5324 must all still be rendered by an ordinary offer. Catches a future
  // change that drops or gates one of them, which no layout-free renderer
  // could otherwise distinguish from the collapse.
  it('renders earnings, trip metrics and both addresses for an ordinary offer', () => {
    const { getByText } = render(<RideOfferPanel {...defaultProps} />);

    expect(getByText('YOUR EARNINGS')).toBeTruthy();
    expect(getByText('15.50')).toBeTruthy();
    expect(getByText('2.5')).toBeTruthy();   // distance_km
    expect(getByText('10')).toBeTruthy();    // duration_minutes
    expect(getByText('123 Main St')).toBeTruthy();
    expect(getByText('456 Elm Ave')).toBeTruthy();
  });

  // Gap #13: a pre-accept decline had no reason at all, so trust & safety
  // had no way to detect a driver refusing a service animal. A long-press
  // on Decline now offers a single, optional flag for that reason. The
  // plain tap (the fast, common decline path) must stay exactly as before.
  describe('decline reason flag (service animal refusal, gap #13)', () => {
    const { showAlert } = require('../../components/AlertDialog');
    const mockShowAlert = showAlert as jest.Mock;

    beforeEach(() => {
      mockShowAlert.mockClear();
    });

    it('a plain tap on Decline calls onDecline with no reason', () => {
      const onDecline = jest.fn();
      const { getByText } = render(<RideOfferPanel {...defaultProps} onDecline={onDecline} />);
      fireEvent.press(getByText('Decline'));
      expect(onDecline).toHaveBeenCalledWith(undefined);
      expect(mockShowAlert).not.toHaveBeenCalled();
    });

    it('long-pressing Decline opens a reason prompt mentioning service animals', () => {
      const { getByText } = render(<RideOfferPanel {...defaultProps} />);
      fireEvent(getByText('Decline'), 'longPress');
      expect(mockShowAlert).toHaveBeenCalledTimes(1);
      const [title, message] = mockShowAlert.mock.calls[0];
      expect(title).toMatch(/reason/i);
      expect(message.toLowerCase()).toContain('service animal');
    });

    it('choosing the service-animal option decline+reports with the shared reason code', () => {
      const onDecline = jest.fn();
      const { getByText } = render(<RideOfferPanel {...defaultProps} onDecline={onDecline} />);
      fireEvent(getByText('Decline'), 'longPress');

      const buttons = mockShowAlert.mock.calls[0][2];
      const serviceAnimalButton = buttons.find((b: any) => /service animal/i.test(b.text));
      expect(serviceAnimalButton).toBeTruthy();
      serviceAnimalButton.onPress();

      expect(onDecline).toHaveBeenCalledWith(DECLINE_REASON_SERVICE_ANIMAL);
    });

    it('choosing Cancel in the reason prompt does not decline', () => {
      const onDecline = jest.fn();
      const { getByText } = render(<RideOfferPanel {...defaultProps} onDecline={onDecline} />);
      fireEvent(getByText('Decline'), 'longPress');

      const buttons = mockShowAlert.mock.calls[0][2];
      const cancelButton = buttons.find((b: any) => b.style === 'cancel');
      expect(cancelButton).toBeTruthy();
      cancelButton.onPress?.();

      expect(onDecline).not.toHaveBeenCalled();
    });
  });

  it('shows the Quiet ride badge when the rider requested a quiet ride', () => {
    const { getByText } = render(
      <RideOfferPanel {...defaultProps} incomingRide={{ ...mockRide, quiet_mode: true }} />,
    );
    expect(getByText('Quiet ride')).toBeTruthy();
  });

  it('does not show the Quiet ride badge when quiet_mode is off', () => {
    const { queryByText } = render(
      <RideOfferPanel {...defaultProps} incomingRide={{ ...mockRide, quiet_mode: false }} />,
    );
    expect(queryByText('Quiet ride')).toBeNull();
  });

  // C35: a scheduled ride dispatches via the identical offer/accept/timeout
  // mechanism as an on-demand ride, but a driver has no way to tell them
  // apart without this badge (ACTION_ITEMS.md C35).
  it('shows the Pre-booked badge when is_scheduled is true', () => {
    const { getByText } = render(
      <RideOfferPanel {...defaultProps} incomingRide={{ ...mockRide, is_scheduled: true }} />,
    );
    expect(getByText('Pre-booked')).toBeTruthy();
  });

  it('does not show the Pre-booked badge when is_scheduled is false', () => {
    const { queryByText } = render(
      <RideOfferPanel {...defaultProps} incomingRide={{ ...mockRide, is_scheduled: false }} />,
    );
    expect(queryByText('Pre-booked')).toBeNull();
  });

  it('does not show the Pre-booked badge when is_scheduled is absent (backward-compatible offer payload)', () => {
    const { queryByText } = render(<RideOfferPanel {...defaultProps} incomingRide={mockRide} />);
    expect(queryByText('Pre-booked')).toBeNull();
  });

  // Design-audit finding: pickup/dropoff addresses were hard-truncated to
  // one line (numberOfLines={1}) with no way to see the rest. Widened to 2
  // lines so most real addresses show in full instead of clipping.
  it('allows pickup/dropoff addresses to wrap onto up to 2 lines instead of hard-truncating to 1', () => {
    const { getByText } = render(<RideOfferPanel {...defaultProps} />);
    expect(getByText('123 Main St').props.numberOfLines).toBe(2);
    expect(getByText('456 Elm Ave').props.numberOfLines).toBe(2);
  });

  describe('isLoading (double-tap guard on accept/decline)', () => {
    it('fires onAccept and onDecline when not loading', () => {
      const onAccept = jest.fn();
      const onDecline = jest.fn();
      const { getByLabelText } = render(
        <RideOfferPanel {...defaultProps} isLoading={false} onAccept={onAccept} onDecline={onDecline} />,
      );
      fireEvent.press(getByLabelText('Accept ride'));
      fireEvent.press(getByLabelText('Decline ride'));
      expect(onAccept).toHaveBeenCalledTimes(1);
      expect(onDecline).toHaveBeenCalledTimes(1);
    });

    it('disables both Accept and Decline while an accept/decline call is in flight', () => {
      const onAccept = jest.fn();
      const onDecline = jest.fn();
      const { getByLabelText } = render(
        <RideOfferPanel {...defaultProps} isLoading={true} onAccept={onAccept} onDecline={onDecline} />,
      );
      // A double-tap while the store's accept/decline request is still
      // in flight must not fire a second request — this is what wiring the
      // real store `isLoading` (instead of a hardcoded `false`) protects.
      fireEvent.press(getByLabelText('Accept ride'));
      fireEvent.press(getByLabelText('Decline ride'));
      expect(onAccept).not.toHaveBeenCalled();
      expect(onDecline).not.toHaveBeenCalled();
    });

    it('shows a spinner instead of the Accept label while loading', () => {
      const { queryByText, UNSAFE_root } = render(
        <RideOfferPanel {...defaultProps} isLoading={true} />,
      );
      expect(queryByText('Accept')).toBeNull();
      expect(UNSAFE_root.findAllByType(require('react-native').ActivityIndicator).length).toBeGreaterThan(0);
    });
  });

  describe('vibration preference (Settings → Sound & Haptics)', () => {
    const { Vibration } = require('react-native');
    const { useAlertPrefsStore } = require('../../store/alertPrefsStore');
    let vibrateSpy: jest.SpyInstance;

    beforeEach(() => {
      vibrateSpy = jest.spyOn(Vibration, 'vibrate').mockImplementation(() => {});
    });
    afterEach(() => {
      vibrateSpy.mockRestore();
      useAlertPrefsStore.setState({ vibration: true });
    });

    it('vibrates on an incoming offer when the pref is ON (default)', () => {
      render(<RideOfferPanel {...defaultProps} />);
      expect(vibrateSpy).toHaveBeenCalled();
    });

    it('does not vibrate on an incoming offer when the pref is OFF', () => {
      useAlertPrefsStore.setState({ vibration: false });
      render(<RideOfferPanel {...defaultProps} />);
      expect(vibrateSpy).not.toHaveBeenCalled();
    });
  });
});


it('shows booked pickup with explicit readable text color', () => {
  const view = render(<RideOfferPanel {...defaultProps} incomingRide={{...mockRide, is_scheduled:true, scheduled_time:'2026-09-15T14:00:00Z'}} />);
  const label = view.getByText(/^Pickup /);
  expect(StyleSheet.flatten(label.props.style).color).toBeTruthy();
});
