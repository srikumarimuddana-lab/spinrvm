/**
 * Ranked blocker #22: the header close ("X") button on the ride-status
 * screen (Finding driver / Driver on the way / etc.) was an icon-only
 * TouchableOpacity with no accessibilityLabel.
 */
import React from 'react';
import { render } from '@testing-library/react-native';
import RideStatusScreen from '../app/ride-status';

jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), replace: jest.fn() }),
  useLocalSearchParams: () => ({ rideId: 'ride-1' }),
}));

jest.mock('../store/rideStore', () => ({
  useRideStore: () => ({
    currentRide: null,
    currentDriver: null,
    fetchRide: jest.fn(),
    cancelRide: jest.fn(),
    simulateDriverArrival: jest.fn(),
    clearRide: jest.fn(),
    updateRideNotes: jest.fn(),
  }),
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('../components/ConfirmSheet', () => () => null);
jest.mock('../components/CancelReasonSheet', () => () => null);
jest.mock('../components/FreeCancelTimer', () => ({ FreeCancelTimer: () => null }));
jest.mock('../store/toastStore', () => ({ showToast: jest.fn() }));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() },
  getApiErrorMessage: () => 'error',
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

describe('RideStatusScreen — header close button accessibility', () => {
  it('has a non-empty accessibilityLabel announcing itself as a button', () => {
    // The screen starts several setInterval/Animated.loop timers (ride poll,
    // pulse/dot animation). Fake timers + an explicit unmount keep those from
    // firing against a torn-down Jest environment after this test completes.
    jest.useFakeTimers();
    const screen = render(<RideStatusScreen />);
    const closeButton = screen.getByLabelText('Close');
    expect(closeButton.props.accessibilityRole).toBe('button');
    screen.unmount();
    jest.useRealTimers();
  });
});
