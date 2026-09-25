import React from 'react';
import { render } from '@testing-library/react-native';
import { Animated } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { DriverIdlePanel } from '../../components/dashboard/DriverIdlePanel';

// Controls the OS Reduce Motion setting as seen through the shared hook.
let mockReduceMotion = false;
jest.mock('@shared/hooks/useReduceMotion', () => ({
  useReduceMotion: () => mockReduceMotion,
}));

// react-native-safe-area-context's `useSafeAreaInsets` throws if no provider is
// in the tree. Wrap every render with a deterministic SafeAreaProvider.
const initialMetrics = {
  frame: { x: 0, y: 0, width: 360, height: 800 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};
const renderWithSafeArea = (ui: React.ReactElement) =>
  render(<SafeAreaProvider initialMetrics={initialMetrics}>{ui}</SafeAreaProvider>);

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: 'View' }));
// DriverIdlePanel reads Constants.executionEnvironment at module scope to
// decide whether the expo-notifications require is safe (Expo Go/web lack
// the native module). 'standalone' keeps that branch inert here — the
// welcome-notification effect isn't what this file is testing.
jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { executionEnvironment: 'standalone' },
  ExecutionEnvironment: { StoreClient: 'storeClient', Standalone: 'standalone', Bare: 'bare' },
}));
jest.mock('expo-notifications', () => ({
  scheduleNotificationAsync: jest.fn(),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve('true')), // welcome notif already sent
  setItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/store/authStore', () => ({
  useAuthStore: (selector: any) =>
    selector({
      driver: {
        status: 'active',
        vehicle_color: 'White',
        vehicle_make: 'Toyota',
        vehicle_model: 'Grand Highlander',
        license_plate: 'KAIRAV',
      },
    }),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({ t: (key: string) => key }),
}));

const defaultProps = {
  isOnline: false,
  onToggleOnline: jest.fn(),
  pulseAnim: { current: 1 },
};

describe('DriverIdlePanel — GO pulse respects Reduce Motion', () => {
  let loopSpy: jest.SpyInstance;

  beforeEach(() => {
    loopSpy = jest.spyOn(Animated, 'loop');
  });
  afterEach(() => {
    loopSpy.mockRestore();
    mockReduceMotion = false;
  });

  it('pulses the GO button while offline when Reduce Motion is off', () => {
    mockReduceMotion = false;
    renderWithSafeArea(<DriverIdlePanel {...defaultProps} isOnline={false} />);
    expect(loopSpy).toHaveBeenCalledTimes(1);
  });

  it('does not start the pulse loop when Reduce Motion is on', () => {
    mockReduceMotion = true;
    renderWithSafeArea(<DriverIdlePanel {...defaultProps} isOnline={false} />);
    expect(loopSpy).not.toHaveBeenCalled();
  });

  it('stops pulsing when Reduce Motion turns on mid-session', () => {
    mockReduceMotion = false;
    const { rerender } = renderWithSafeArea(
      <DriverIdlePanel {...defaultProps} isOnline={false} />,
    );
    expect(loopSpy).toHaveBeenCalledTimes(1);

    mockReduceMotion = true;
    rerender(
      <SafeAreaProvider initialMetrics={initialMetrics}>
        <DriverIdlePanel {...defaultProps} isOnline={false} />
      </SafeAreaProvider>,
    );
    // No new loop was started after the setting flipped.
    expect(loopSpy).toHaveBeenCalledTimes(1);
  });
});
