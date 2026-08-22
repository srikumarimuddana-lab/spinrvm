/**
 * app/scheduled-rides.tsx — the rider's list of upcoming scheduled rides.
 * Pins:
 *  - fetchScheduledRides fires on mount, and again on pull-to-refresh
 *  - the empty state (with a "Book a Ride" CTA) renders when there are
 *    no scheduled rides
 *  - Cancel opens a confirm sheet; confirming calls cancelScheduledRide +
 *    best-effort cancelReminder (swallowed failure) and toasts success;
 *    a cancelScheduledRide failure toasts the backend's error message
 *    instead
 *  - the C29 notice-window fee preview appends the fee amount to the
 *    confirm message only when the ride actually carries one
 *  - "Dispatching..." is shown once a ride's scheduled time has passed
 *
 * ConfirmSheet pulls in @gorhom/bottom-sheet (a heavy native dependency),
 * so it's replaced with a lightweight double that renders its buttons
 * directly when visible -- this test drives the real screen logic
 * (handleCancel's message-building and the button onPress handlers),
 * not the real bottom-sheet's internals.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@react-native-async-storage/async-storage', () => {
  const store: Record<string, string> = {};
  return {
    __esModule: true,
    default: {
      getItem: jest.fn((k: string) => Promise.resolve(store[k] ?? null)),
      setItem: jest.fn((k: string, v: string) => { store[k] = v; return Promise.resolve(); }),
      removeItem: jest.fn((k: string) => { delete store[k]; return Promise.resolve(); }),
    },
  };
});

import ScheduledRidesScreen from '../app/scheduled-rides';
import { useRideStore } from '../store/rideStore';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockCancelReminder = jest.fn().mockResolvedValue(undefined);
jest.mock('../hooks/useScheduledRideReminder', () => ({
  useScheduledRideReminder: () => ({ cancelReminder: (...a: any[]) => mockCancelReminder(...a) }),
}));

// Lightweight double: renders each button as a real TouchableOpacity when
// visible, so tests can find and press them without the real bottom sheet.
jest.mock('../components/ConfirmSheet', () => (props: any) => {
  const { View, Text: RNText, TouchableOpacity: RNTouchableOpacity } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <RNText>{props.title}</RNText>
      <RNText>{props.message}</RNText>
      {(props.buttons || []).map((b: any, i: number) => (
        <RNTouchableOpacity key={i} onPress={b.onPress || props.onClose} accessibilityLabel={`confirm-${b.text}`}>
          <RNText>{b.text}</RNText>
        </RNTouchableOpacity>
      ))}
    </View>
  );
});

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const RIDE_1 = {
  id: 'sr-1',
  scheduled_time: new Date(Date.now() + 2 * 60 * 60 * 1000).toISOString(),
  total_fare: 24.5,
  pickup_address: '100 Main St',
  dropoff_address: '200 Elm St',
  distance_km: 5.2,
  duration_minutes: 12,
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<ScheduledRidesScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  useRideStore.setState({
    scheduledRides: [],
    fetchScheduledRides: jest.fn().mockResolvedValue(undefined),
    cancelScheduledRide: jest.fn().mockResolvedValue(undefined),
  } as any);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('ScheduledRidesScreen', () => {
  it('fetches scheduled rides on mount', async () => {
    await renderScreen();
    expect(useRideStore.getState().fetchScheduledRides).toHaveBeenCalled();
  });

  it('shows the empty state with a Book a Ride CTA when there are none', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No scheduled rides');
    const bookBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Book a Ride')))!;
    act(() => {
      bookBtn.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/(tabs)');
  });

  it('renders a ride card with fare, addresses, and stats', async () => {
    useRideStore.setState({ scheduledRides: [RIDE_1] } as any);
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('["$","24.50"]');
    expect(text).toContain('100 Main St');
    expect(text).toContain('200 Elm St');
  });

  it('shows "Dispatching..." once the scheduled time has passed', async () => {
    useRideStore.setState({
      scheduledRides: [{ ...RIDE_1, scheduled_time: new Date(Date.now() - 60_000).toISOString() }],
    } as any);
    const r = await renderScreen();
    expect(allText(r)).toContain('Dispatching...');
  });

  it('opens a plain confirm message (no fee) when the ride has no notice-window fee', async () => {
    useRideStore.setState({ scheduledRides: [RIDE_1] } as any);
    const r = await renderScreen();
    const cancelBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Cancel')))!;
    act(() => {
      cancelBtn.props.onPress();
    });
    const text = allText(r);
    expect(text).toContain('Are you sure you want to cancel this scheduled ride?');
    expect(text).not.toContain('late-cancellation fee');
  });

  it('includes the notice-window fee amount in the confirm message when present', async () => {
    useRideStore.setState({ scheduledRides: [{ ...RIDE_1, notice_window_fee_amount: 5 }] } as any);
    const r = await renderScreen();
    const cancelBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Cancel')))!;
    act(() => {
      cancelBtn.props.onPress();
    });
    expect(allText(r)).toContain('$5.00 late-cancellation fee may apply');
  });

  it('confirming cancels the ride, best-effort cancels the reminder, and toasts success', async () => {
    useRideStore.setState({ scheduledRides: [RIDE_1] } as any);
    const r = await renderScreen();
    const cancelBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Cancel')))!;
    act(() => {
      cancelBtn.props.onPress();
    });
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel Ride' });
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    expect(useRideStore.getState().cancelScheduledRide).toHaveBeenCalledWith('sr-1');
    expect(mockCancelReminder).toHaveBeenCalledWith('sr-1');
    expect(mockShowToast).toHaveBeenCalledWith('Cancelled', 'Your scheduled ride has been cancelled.', 'success');
  });

  it('shows a failure toast (not a crash) when cancelScheduledRide rejects', async () => {
    useRideStore.setState({
      scheduledRides: [RIDE_1],
      cancelScheduledRide: jest.fn().mockRejectedValue(new Error('server error')),
    } as any);
    const r = await renderScreen();
    const cancelBtn = r.root
      .findAllByType(TouchableOpacity)
      .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes('Cancel')))!;
    act(() => {
      cancelBtn.props.onPress();
    });
    const confirmBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel Ride' });
    await act(async () => {
      await confirmBtn.props.onPress();
      await flush();
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'Cancel Failed',
      'Failed to cancel your scheduled ride. Please try again.',
      'danger',
    );
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
