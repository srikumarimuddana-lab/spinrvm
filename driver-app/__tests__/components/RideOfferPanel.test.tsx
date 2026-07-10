import React from 'react';
import { render } from '@testing-library/react-native';
import { RideOfferPanel } from '../../components/panels/RideOfferPanel';

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
    const { getByText } = render(<RideOfferPanel {...defaultProps} />);
    expect(getByText('10 min')).toBeTruthy();
  });

  it('shows countdown seconds', () => {
    const { getByText } = render(<RideOfferPanel {...defaultProps} countdownSeconds={9} />);
    expect(getByText('9')).toBeTruthy();
  });

  it('renders nothing when incomingRide is null', () => {
    const { toJSON } = render(<RideOfferPanel {...defaultProps} incomingRide={null} />);
    expect(toJSON()).toBeNull();
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
