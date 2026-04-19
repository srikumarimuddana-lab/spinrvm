import React from 'react';
import { Animated } from 'react-native';
import { render } from '@testing-library/react-native';
import { ActiveRidePanel } from '../../components/dashboard/ActiveRidePanel';

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

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));

jest.mock('@shared/components/CustomAlert', () => ({
  __esModule: true,
  default: () => null,
}));

const mockRide = {
  id: 'ride-001',
  pickup_address: '123 Main St',
  dropoff_address: '456 Elm Ave',
  pickup_lat: 52.1333,
  pickup_lng: -106.6667,
  dropoff_lat: 52.15,
  dropoff_lng: -106.65,
  total_fare: 15.0,
  driver_earnings: 15.0,
  distance_km: 2.5,
  duration_minutes: 10,
  status: 'driver_assigned',
};

const mockRider = {
  first_name: 'Jane',
  last_name: 'Doe',
  rating: 4.8,
  phone: '+15061234567',
};

const defaultProps = {
  rideState: 'navigating_to_pickup' as const,
  ride: mockRide,
  rider: mockRider,
  driverLocation: null,
  isLoading: false,
  otpInput: '',
  setOtpInput: jest.fn(),
  onVerifyOTP: jest.fn(),
  onNavigate: jest.fn(),
  onArriveAtPickup: jest.fn(),
  onStartRide: jest.fn(),
  onCompleteRide: jest.fn(),
  onCancelRide: jest.fn(),
  routeEtaMinutes: null,
  routeDistanceKm: null,
  slideUpAnim: new Animated.Value(0),
  fadeAnim: new Animated.Value(1),
  distanceToPickup: null,
};

describe('ActiveRidePanel', () => {
  it('renders without crashing', () => {
    const { toJSON } = render(<ActiveRidePanel {...defaultProps} />);
    expect(toJSON()).not.toBeNull();
  });

  it('shows rider name', () => {
    const { getByText } = render(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('Jane D.')).toBeTruthy();
  });

  it('shows earnings amount', () => {
    const { getAllByText } = render(<ActiveRidePanel {...defaultProps} />);
    // Earnings appears in both the status pill and the trip info row
    expect(getAllByText('$15.00').length).toBeGreaterThan(0);
  });

  it('shows pickup address', () => {
    const { getByText } = render(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('123 Main St')).toBeTruthy();
  });

  it('shows cancel ride option during navigating_to_pickup', () => {
    const { getByText } = render(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('Cancel Ride')).toBeTruthy();
  });

  it('renders nothing when ride is null', () => {
    const { toJSON } = render(<ActiveRidePanel {...defaultProps} ride={null} />);
    expect(toJSON()).toBeNull();
  });
});
