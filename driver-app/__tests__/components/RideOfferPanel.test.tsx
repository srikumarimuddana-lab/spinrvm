import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
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

  afterEach(() => {
    jest.runOnlyPendingTimers();
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
