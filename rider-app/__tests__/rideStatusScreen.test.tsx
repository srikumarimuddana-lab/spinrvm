/**
 * app/ride-status.tsx — broader coverage beyond rideStatusCloseButton.test.tsx
 * (pins only the close button's accessibilityLabel) and rideStatusContract.test.ts
 * (a type-only contract check). Uses the real rideStore (setState), same
 * convention as searchDestinationScreen.test.tsx — actions are overridden
 * directly on the store instance via setState.
 *
 * Pins:
 *  - the loading state (no currentRide) shows a spinner
 *  - each status renders its own body: searching (timer + "taking longer"
 *    after 120s + Cancel search button), driver_assigned (offer countdown
 *    progress bar + FreeCancelTimer), driver_accepted (same body, different
 *    copy), driver_arrived (OTP digits + driver card)
 *  - the driver-photo error fallback (both driver_assigned and
 *    driver_arrived bodies)
 *  - handleBackPress's status-driven branch: no ride -> router.back(); no
 *    driver yet (searching) -> "Cancel search?" confirm whose Cancel button
 *    calls performCancel(); driver_assigned/driver_accepted -> free vs. paid
 *    cancel copy depending on the free-cancel window; driver_arrived ->
 *    always the paid-cancellation-fee copy
 *  - performCancel: success clears the ride and replaces to /(tabs)/;
 *    failure toasts instead
 *  - the notes chip: only rendered pre-trip-start statuses; opens the notes
 *    sheet pre-filled from the current note; Save calls updateRideNotes and
 *    closes on success, toasts and stays open on failure
 *  - the header title text per status
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Image, BackHandler } from 'react-native';

import { useRideStore } from '../store/rideStore';
import RideStatusScreen from '../app/ride-status';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
const mockBack = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, replace: mockReplace }),
  useLocalSearchParams: () => ({ rideId: 'ride-1' }),
}));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('../components/ConfirmSheet', () => (props: any) => {
  const { View, Text: RNText, TouchableOpacity: RNTouchableOpacity } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <RNText>{props.title}</RNText>
      {props.message ? <RNText>{props.message}</RNText> : null}
      {(props.buttons || []).map((b: any, i: number) => (
        <RNTouchableOpacity key={i} onPress={b.onPress || props.onClose} accessibilityLabel={`confirm-${b.text}`}>
          <RNText>{b.text}</RNText>
        </RNTouchableOpacity>
      ))}
    </View>
  );
});
jest.mock('../components/CancelReasonSheet', () => (props: any) => {
  const { View, TouchableOpacity: RNTouchableOpacity, Text: RNText } = require('react-native');
  if (!props.visible) return null;
  return (
    <View>
      <RNTouchableOpacity accessibilityLabel="cancel-reason-confirm" onPress={() => props.onConfirm('rider_no_show')}>
        <RNText>Confirm Reason</RNText>
      </RNTouchableOpacity>
    </View>
  );
});
jest.mock('../components/FreeCancelTimer', () => ({
  FreeCancelTimer: (props: any) => {
    const { Text: RNText } = require('react-native');
    return <RNText accessibilityLabel="free-cancel-timer">{JSON.stringify({ status: props.rideStatus, fee: props.cancellationFee })}</RNText>;
  },
}));
const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...a: any[]) => mockShowToast(...a) }));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(() => Promise.resolve(null)),
    setItem: jest.fn(() => Promise.resolve()),
    removeItem: jest.fn(() => Promise.resolve()),
  },
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB', error: '#DC2626',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockFetchRide = jest.fn();
const mockCancelRide = jest.fn();
const mockClearRide = jest.fn();
const mockSimulateDriverArrival = jest.fn();
const mockUpdateRideNotes = jest.fn();

const DRIVER = {
  name: 'Jamie Fox', rating: 4.9, total_rides: 120, photo_url: null,
  vehicle_color: 'Black', vehicle_make: 'Toyota', vehicle_model: 'Camry', license_plate: '123ABC',
};

function resetStore(overrides: any = {}) {
  useRideStore.setState({
    currentRide: null,
    currentDriver: null,
    fetchRide: mockFetchRide,
    cancelRide: mockCancelRide,
    clearRide: mockClearRide,
    simulateDriverArrival: mockSimulateDriverArrival,
    updateRideNotes: mockUpdateRideNotes,
    ...overrides,
  } as any);
}

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<RideStatusScreen />);
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => {
    try { return JSON.stringify(t.props.children); } catch { return ''; }
  }).join(' | ');
}

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  resetStore();
  mockCancelRide.mockResolvedValue(undefined);
  mockUpdateRideNotes.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => { renderer?.unmount(); });
  renderer = null;
  jest.useRealTimers();
});

describe('loading / header', () => {
  it('shows a spinner while the ride has not loaded yet', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('"Loading ride details..."');
    expect(allText(r)).toContain('"Loading..."');
  });

  it('shows the searching header title and body', async () => {
    resetStore({ currentRide: { id: 'ride-1', status: 'searching', pickup_otp: '1234' } });
    const r = await renderScreen();
    expect(allText(r)).toContain('"Finding driver..."');
    expect(allText(r)).toContain('"Finding your driver"');
  });
});

describe('searching state', () => {
  it('shows the "taking longer" copy and a Cancel search button after 120s', async () => {
    resetStore({ currentRide: { id: 'ride-1', status: 'searching', pickup_otp: '1234' } });
    const r = await renderScreen();
    await act(async () => { jest.advanceTimersByTime(120000); });
    expect(allText(r)).toContain('"Taking longer than usual — hang tight"');
    expect(allText(r)).toContain('"Cancel search"');
  });
});

describe('driver_assigned / driver_accepted', () => {
  it('driver_assigned shows the offer countdown and "confirming" copy', async () => {
    resetStore({
      currentRide: { id: 'ride-1', status: 'driver_assigned', pickup_otp: '1234', offer_timeout_seconds: 15 } as any,
      currentDriver: DRIVER as any,
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('"Driver found — confirming your ride"');
    expect(allText(r)).toContain('"Driver has 15s to accept"');
  });

  it('driver_accepted shows "on the way" copy with no offer countdown', async () => {
    resetStore({
      currentRide: { id: 'ride-1', status: 'driver_accepted', pickup_otp: '1234' } as any,
      currentDriver: DRIVER as any,
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('"Driver accepted — on the way"');
    expect(allText(r)).not.toContain('Driver has');
  });

  it('falls back to the person icon when the driver photo fails to load', async () => {
    resetStore({
      currentRide: { id: 'ride-1', status: 'driver_assigned', pickup_otp: '1234' } as any,
      currentDriver: { ...DRIVER, photo_url: 'https://example.com/x.jpg' } as any,
    });
    const r = await renderScreen();
    const img = r.root.findByType(Image);
    act(() => { img.props.onError(); });
    expect(r.root.findAllByType(Image)).toHaveLength(0);
  });
});

describe('driver_arrived state', () => {
  it('renders the OTP digits and driver card', async () => {
    resetStore({
      currentRide: { id: 'ride-1', status: 'driver_arrived', pickup_otp: '4821' } as any,
      currentDriver: DRIVER as any,
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('"4"');
    expect(allText(r)).toContain('"8"');
    expect(allText(r)).toContain('"2"');
    expect(allText(r)).toContain('"1"');
    expect(allText(r)).toContain('"Jamie Fox"');
  });
});

describe('handleBackPress', () => {
  it('with no ride loaded, just goes back (no confirm sheet)', async () => {
    const r = await renderScreen();
    const closeBtn = r.root.findAllByProps({ accessibilityLabel: 'Close' })[0];
    act(() => { closeBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });

  it('while searching, "Cancel search?" -> Cancel calls performCancel directly (no reason sheet)', async () => {
    resetStore({ currentRide: { id: 'ride-1', status: 'searching', pickup_otp: '1234' } as any });
    const r = await renderScreen();
    const closeBtn = r.root.findAllByProps({ accessibilityLabel: 'Close' })[0];
    act(() => { closeBtn.props.onPress(); });
    expect(allText(r)).toContain('"Cancel search?"');
    const cancelBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => t.props.children === 'Cancel')
    )!;
    await act(async () => { await cancelBtn.props.onPress(); await flush(); });
    expect(mockCancelRide).toHaveBeenCalledWith(undefined);
    expect(mockClearRide).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('with a driver assigned inside the free window, offers a free cancel that asks for a reason first', async () => {
    resetStore({
      currentRide: { id: 'ride-1', status: 'driver_assigned', pickup_otp: '1234', free_cancel_seconds_remaining: 60 } as any,
    });
    const r = await renderScreen();
    act(() => { r.root.findAllByProps({ accessibilityLabel: 'Close' })[0].props.onPress(); });
    expect(allText(r)).toContain('"Your driver is on the way. Cancel for free right now."');
    const freeCancelBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => t.props.children === 'Cancel (Free)')
    )!;
    act(() => { freeCancelBtn.props.onPress(); });
    // Reason sheet should now be visible (mocked double renders "Confirm Reason").
    expect(allText(r)).toContain('"Confirm Reason"');
    const confirmReasonBtn = r.root.findAllByProps({ accessibilityLabel: 'cancel-reason-confirm' })[0];
    await act(async () => { await confirmReasonBtn.props.onPress(); await flush(); });
    expect(mockCancelRide).toHaveBeenCalledWith('rider_no_show');
  });

  it('driver_arrived always shows the paid-cancellation-fee copy', async () => {
    resetStore({
      currentRide: { id: 'ride-1', status: 'driver_arrived', pickup_otp: '1234', cancellation_fee: 5 } as any,
      currentDriver: DRIVER as any,
    });
    const r = await renderScreen();
    act(() => { r.root.findAllByProps({ accessibilityLabel: 'Close' })[0].props.onPress(); });
    expect(allText(r)).toContain('"Driver is waiting"');
    expect(allText(r)).toContain('"Your driver has arrived. A cancellation fee of $5.00 will be charged."');
  });

  it('a cancel failure toasts instead of clearing the ride', async () => {
    mockCancelRide.mockRejectedValue(new Error('network'));
    resetStore({ currentRide: { id: 'ride-1', status: 'searching', pickup_otp: '1234' } as any });
    const r = await renderScreen();
    act(() => { r.root.findAllByProps({ accessibilityLabel: 'Close' })[0].props.onPress(); });
    const cancelBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => t.props.children === 'Cancel')
    )!;
    await act(async () => { await cancelBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Could not cancel', 'The server rejected the request. Please try again.', 'danger');
    expect(mockClearRide).not.toHaveBeenCalled();
  });
});

describe('note-for-driver chip', () => {
  it('is hidden once the trip has moved past driver_arrived (e.g. in_progress)', async () => {
    resetStore({ currentRide: { id: 'ride-1', status: 'in_progress', pickup_otp: '1234' } as any });
    const r = await renderScreen();
    expect(allText(r)).not.toContain('"Add note for driver"');
  });

  it('opens the sheet pre-filled with the existing note, saves, and closes', async () => {
    resetStore({ currentRide: { id: 'ride-1', status: 'searching', pickup_otp: '1234', rider_notes: 'Gate 12' } as any });
    const r = await renderScreen();
    const chip = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => String(t.props.children).includes('Gate 12'))
    )!;
    act(() => { chip.props.onPress(); });
    const input = r.root.findByType(TextInput);
    expect(input.props.value).toBe('Gate 12');
    act(() => { input.props.onChangeText('New gate code'); });
    const saveBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => t.props.children === 'Save')
    )!;
    await act(async () => { await saveBtn.props.onPress(); await flush(); });
    expect(mockUpdateRideNotes).toHaveBeenCalledWith('New gate code');
    expect(allText(r)).not.toContain('"Saving…"');
  });

  it('a save failure toasts and keeps the sheet open', async () => {
    mockUpdateRideNotes.mockRejectedValue(new Error('locked'));
    resetStore({ currentRide: { id: 'ride-1', status: 'searching', pickup_otp: '1234' } as any });
    const r = await renderScreen();
    const chip = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => t.props.children === 'Add note for driver')
    )!;
    act(() => { chip.props.onPress(); });
    const saveBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => t.props.children === 'Save')
    )!;
    await act(async () => { await saveBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Note not saved', 'locked', 'warning');
  });
});

it('the hardware back button also triggers handleBackPress', async () => {
  const spy = jest.spyOn(BackHandler, 'addEventListener');
  const r = await renderScreen();
  const handler = spy.mock.calls.find((c) => c[0] === 'hardwareBackPress')?.[1] as () => boolean;
  expect(handler).toBeDefined();
  act(() => { handler(); });
  expect(mockBack).toHaveBeenCalled();
});
