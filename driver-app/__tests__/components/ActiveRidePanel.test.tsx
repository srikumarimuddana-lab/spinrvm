import React from 'react';
import { Animated, Linking } from 'react-native';
import { render, fireEvent, waitFor } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ActiveRidePanel } from '../../components/dashboard/ActiveRidePanel';

// react-native-safe-area-context's `useSafeAreaInsets` throws if no provider is
// in the tree. Wrap every render with a deterministic SafeAreaProvider.
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

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

// Mock the router like TripCompletedPanel.test.tsx does — importing the real
// expo-router drags the whole react-navigation tree through babel, which
// fails on react-native's flow-typed internals under jest.
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn(), back: jest.fn() }),
}));

// Modal-based children pull react-native's flow-typed native specs through
// babel codegen, which the jest transform can't parse. The panel's own
// layout is what's under test, so stub them out.
jest.mock('../../components/CancelReasonSheet', () => ({
  __esModule: true,
  default: () => null,
}));
jest.mock('../../components/AlertDialog', () => ({
  showAlert: jest.fn(),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));

// Driver's saved navigation-app choice — mutate `mockNavApp` per test.
let mockNavApp = 'default';
jest.mock('../../store/navStore', () => ({
  useNavStore: () => ({ navApp: mockNavApp, loadNavApp: jest.fn() }),
}));

jest.mock('../../hooks/useToast', () => ({
  showToast: jest.fn(),
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
    const { toJSON } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    expect(toJSON()).not.toBeNull();
  });

  it('shows rider name', () => {
    const { getByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('Jane D.')).toBeTruthy();
  });

  it('shows earnings amount', () => {
    const { getAllByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    // Earnings appears in both the status pill and the trip info row
    expect(getAllByText('$15.00').length).toBeGreaterThan(0);
  });

  it('shows pickup address', () => {
    const { getByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('123 Main St')).toBeTruthy();
  });

  it('shows cancel ride option during navigating_to_pickup', () => {
    const { getByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    expect(getByText('Cancel Ride')).toBeTruthy();
  });

  it('renders the draggable grab area expanded by default', () => {
    const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
    // Label flips to "Expand ride details" when collapsed; expanded is the default.
    expect(getByLabelText('Collapse ride details')).toBeTruthy();
  });

  it('renders nothing when ride is null', () => {
    // Render inside SafeAreaProvider (the panel calls useSafeAreaInsets before
    // its early-return); assert the panel produced no panel-specific output by
    // checking that fields populated from a non-null ride are absent.
    const { queryByText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} ride={null} />);
    expect(queryByText('Jane D.')).toBeNull();
    expect(queryByText('Cancel Ride')).toBeNull();
  });

  describe('navigation app preference', () => {
    let openURL: jest.SpyInstance;
    let canOpenURL: jest.SpyInstance;
    beforeEach(() => {
      openURL = jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never);
      // Default: the chosen app IS installed.
      canOpenURL = jest.spyOn(Linking, 'canOpenURL').mockResolvedValue(true as never);
    });
    afterEach(() => {
      mockNavApp = 'default';
      openURL.mockRestore();
      canOpenURL.mockRestore();
    });

    it('launches Waze deep link when the driver chose Waze and it is installed', async () => {
      mockNavApp = 'waze';
      const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
      fireEvent.press(getByLabelText('activeRide.navigateToPickup'));
      await waitFor(() =>
        expect(openURL).toHaveBeenCalledWith('waze://?ll=52.1333,-106.6667&navigate=yes'),
      );
    });

    it('launches the Google Maps app deep link when chosen and installed', async () => {
      mockNavApp = 'google';
      const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
      fireEvent.press(getByLabelText('activeRide.navigateToPickup'));
      await waitFor(() =>
        expect(openURL).toHaveBeenCalledWith('comgooglemaps://?daddr=52.1333,-106.6667&directionsmode=driving'),
      );
    });

    it('falls back to the default maps app when the chosen app is NOT installed', async () => {
      mockNavApp = 'waze';
      canOpenURL.mockResolvedValue(false as never); // Waze not installed
      const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
      fireEvent.press(getByLabelText('activeRide.navigateToPickup'));
      await waitFor(() => expect(openURL).toHaveBeenCalled());
      // Never the unhandled waze:// scheme — falls back to a real maps URL.
      for (const call of openURL.mock.calls) {
        expect((call[0] as string).startsWith('waze://')).toBe(false);
      }
    });

    it('does not use Waze/Google deep links when the driver left it on Default', async () => {
      mockNavApp = 'default';
      const { getByLabelText } = renderWithSafeArea(<ActiveRidePanel {...defaultProps} />);
      fireEvent.press(getByLabelText('activeRide.navigateToPickup'));
      await waitFor(() => expect(openURL).toHaveBeenCalled());
      const calledWith = openURL.mock.calls[0][0] as string;
      expect(calledWith.startsWith('waze://')).toBe(false);
      expect(calledWith.startsWith('comgooglemaps://')).toBe(false);
    });
  });
});
