import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { DriverIdlePanel } from '../../components/dashboard/DriverIdlePanel';

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

describe('DriverIdlePanel — collapsible hud (round 7)', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it('reports expanded=true on mount and stays expanded while offline', () => {
    const onExpandedChange = jest.fn();
    renderWithSafeArea(
      <DriverIdlePanel {...defaultProps} isOnline={false} onExpandedChange={onExpandedChange} />,
    );
    expect(onExpandedChange).toHaveBeenCalledWith(true);
    act(() => { jest.advanceTimersByTime(5000); });
    // Offline never auto-collapses — only the last call matters.
    expect(onExpandedChange).toHaveBeenLastCalledWith(true);
  });

  it('auto-collapses ~2s after going online, reporting expanded=false', () => {
    const onExpandedChange = jest.fn();
    const { rerender } = renderWithSafeArea(
      <DriverIdlePanel {...defaultProps} isOnline={false} onExpandedChange={onExpandedChange} />,
    );
    rerender(
      <SafeAreaProvider initialMetrics={initialMetrics}>
        <DriverIdlePanel {...defaultProps} isOnline={true} onExpandedChange={onExpandedChange} />
      </SafeAreaProvider>,
    );
    expect(onExpandedChange).toHaveBeenLastCalledWith(true); // not yet collapsed
    act(() => { jest.advanceTimersByTime(2000); });
    expect(onExpandedChange).toHaveBeenLastCalledWith(false);
  });

  it('tapping the collapsed pill re-expands, then auto-recollapses', () => {
    const onExpandedChange = jest.fn();
    const { getByA11yHint, rerender } = renderWithSafeArea(
      <DriverIdlePanel {...defaultProps} isOnline={false} onExpandedChange={onExpandedChange} />,
    );
    rerender(
      <SafeAreaProvider initialMetrics={initialMetrics}>
        <DriverIdlePanel {...defaultProps} isOnline={true} onExpandedChange={onExpandedChange} />
      </SafeAreaProvider>,
    );
    act(() => { jest.advanceTimersByTime(2000); });
    expect(onExpandedChange).toHaveBeenLastCalledWith(false);

    // Both the fading full-content status pill (role="text") and the
    // collapsed pill (role="button") carry the same accessibilityLabel
    // during the crossfade — accessibilityHint is unique to the tappable
    // collapsed pill.
    fireEvent.press(getByA11yHint('Tap to show vehicle details'));
    expect(onExpandedChange).toHaveBeenLastCalledWith(true);

    act(() => { jest.advanceTimersByTime(2500); });
    expect(onExpandedChange).toHaveBeenLastCalledWith(false);
  });
});
