import React from 'react';
import { render } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { TripCompletedPanel } from '../../components/dashboard/TripCompletedPanel';

// react-native-safe-area-context's `useSafeAreaInsets` throws if no provider is
// in the tree. Wrap every render with a deterministic SafeAreaProvider so
// tests don't depend on the host app's layout chrome.
const initialMetrics = {
  frame: { x: 0, y: 0, width: 360, height: 800 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};
const renderWithSafeArea = (ui: React.ReactElement) =>
  render(
    <SafeAreaProvider initialMetrics={initialMetrics}>{ui}</SafeAreaProvider>,
  );

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

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));

jest.mock('@shared/components/CustomAlert', () => ({
  __esModule: true,
  default: () => null,
}));

const mockCompletedRide = {
  id: 'ride-001',
  base_fare: 3.0,
  distance_fare: 7.5,
  time_fare: 2.5,
  booking_fee: 0.5,
  total_fare: 15.0,
  driver_earnings: 12.0,
  distance_km: 5.2,
  duration_minutes: 18,
  pickup_address: '123 Main St',
  dropoff_address: '456 Elm Ave',
};

const defaultProps = {
  completedRide: mockCompletedRide,
  onDone: jest.fn(),
  onRateRider: jest.fn().mockResolvedValue(undefined),
};

describe('TripCompletedPanel', () => {
  it('renders without crashing', () => {
    const { toJSON } = renderWithSafeArea(<TripCompletedPanel {...defaultProps} />);
    expect(toJSON()).not.toBeNull();
  });

  it('shows driver earnings in summary', () => {
    const { getByText } = renderWithSafeArea(<TripCompletedPanel {...defaultProps} />);
    expect(getByText('$12.00')).toBeTruthy();
  });

  it('shows fare breakdown labels', () => {
    const { getByText } = renderWithSafeArea(<TripCompletedPanel {...defaultProps} />);
    expect(getByText('tripCompleted.baseFare')).toBeTruthy();
    expect(getByText('tripCompleted.yourEarnings')).toBeTruthy();
  });

  it('renders rating section with prompt label', () => {
    const { getByText } = renderWithSafeArea(<TripCompletedPanel {...defaultProps} />);
    // The rating section shows a "how was your rider?" label (translated key)
    expect(getByText('tripCompleted.howWasRider')).toBeTruthy();
  });

  it('shows trip stats (distance and duration)', () => {
    const { getByText } = renderWithSafeArea(<TripCompletedPanel {...defaultProps} />);
    expect(getByText('5.2 km')).toBeTruthy();
    expect(getByText('18 min')).toBeTruthy();
  });

  it('renders nothing when completedRide is null', () => {
    // Render inside SafeAreaProvider (the panel calls useSafeAreaInsets before
    // its early-return); assert the panel produced no panel-specific output by
    // checking that an always-rendered field from a non-null ride is absent.
    const { queryByText } = renderWithSafeArea(<TripCompletedPanel {...defaultProps} completedRide={null} />);
    expect(queryByText('$12.00')).toBeNull();
    expect(queryByText('tripCompleted.baseFare')).toBeNull();
  });
});
