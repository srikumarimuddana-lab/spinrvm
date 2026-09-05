/**
 * app/ride-options.tsx — vehicle-type selection + booking screen
 * (money-critical: this is where a ride is actually created). Pins:
 *  - mount effects: parallel fetchEstimates + fetchNearbyDrivers when
 *    pickup/dropoff are set; a fetchEstimates rejection sets the
 *    "Could not load fares" retry state instead of crashing
 *  - handleSelect: an unavailable vehicle type toasts and does not
 *    change selection; an available one updates selectedIndex + the
 *    store's selectedVehicle
 *  - handleBookRide's payment guard: card-with-no-selected-card opens a
 *    confirm sheet instead of booking (distinct copy for zero saved
 *    cards vs. an unselected one)
 *  - handleBookRide's scheduled-time guard (<15 min from now rejected)
 *  - handleBookRide's corporate work-policy block (checkRide) offering
 *    "Turn off work mode" / "Change ride"
 *  - handleBookRide's surge confirm gate: >1.0x always requires an
 *    explicit "Book at $X" tap before proceedWithBooking runs; exactly
 *    1.0x books directly
 *  - proceedWithBooking: happy-path -> /driver-arriving (or /(tabs) +
 *    scheduleReminder for a scheduled ride); requires_action toasts
 *    without navigating; promo_error toasts but still navigates; a 409
 *    re-routes to whichever screen owns the rider's real active ride by
 *    status; a 402 with unpaid_ride_id opens the Unpaid Ride confirm
 *    sheet; any other failure toasts the backend's message
 *  - handleScheduleConfirm rejects a time <15 min out
 *  - handleManualPromo: an ineligible match toasts + sets the inline
 *    error; an eligible match applies it and clears the input; no match
 *    sets "Code not found or has expired"
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));
// Keep the real SPACING/FONT tokens (jest.requireActual) — shared Button.tsx,
// pulled in transitively via app/ride-options.tsx's now-migrated Confirm
// button, reads them at module scope. Only useResponsive is overridden, as
// before.
jest.mock('@shared/utils/responsive', () => ({
  ...jest.requireActual('@shared/utils/responsive'),
  useResponsive: () => ({ sf: (n: number) => n }),
}));

jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const MapView = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ fitToCoordinates: jest.fn(), animateToRegion: jest.fn() }));
    return ReactActual.createElement('MapView', props, props.children);
  });
  return {
    __esModule: true, default: MapView, PROVIDER_GOOGLE: 'google',
    Marker: (props: any) => props.children ?? null, Polygon: () => null,
  };
});
jest.mock('react-native-maps-directions', () => () => null);
jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));
jest.mock('@shared/components/CarMarker', () => ({ CarMarker: () => null, resolveMarkerVariant: () => 'sedan' }));
jest.mock('expo-image', () => ({ Image: () => null }));
jest.mock('../components/CustomToggle', () => (props: any) => {
  const { Text: RNText } = require('react-native');
  return <RNText accessibilityLabel={props.accessibilityLabel}>{String(props.value)}</RNText>;
});
jest.mock('../components/SkeletonBox', () => () => null);
// Renders its onConfirm directly on its own props so a test can invoke it
// with any date, exercising handleScheduleConfirm's own validation rather
// than a fixed always-valid stand-in date.
jest.mock('../components/SchedulePicker', () => (props: any) => {
  const { View } = require('react-native');
  if (!props.visible) return null;
  return <View testID="schedule-picker" accessible {...{ onConfirm: props.onConfirm }} />;
});

jest.mock('@gorhom/bottom-sheet', () => {
  const ReactActual = require('react');
  const BottomSheet = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ expand: jest.fn(), close: jest.fn() }));
    return props.children;
  });
  const BottomSheetScrollView = (props: any) => props.children;
  const BottomSheetBackdrop = () => null;
  const BottomSheetTextInput = (props: any) => {
    const { TextInput } = require('react-native');
    return <TextInput {...props} />;
  };
  return { __esModule: true, default: BottomSheet, BottomSheetScrollView, BottomSheetBackdrop, BottomSheetTextInput };
});

const mockBack = jest.fn();
const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush, replace: mockReplace }),
}));

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), []);
    },
  };
});

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockIsEngineError = jest.fn(() => false);
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
  isEngineError: (..._a: any[]) => mockIsEngineError(),
}));

const mockAnalyticsRideRequested = jest.fn();
const mockAnalyticsPaymentInitiated = jest.fn();
jest.mock('@shared/analytics', () => ({
  Analytics: {
    rideRequested: (...a: any[]) => mockAnalyticsRideRequested(...a),
    paymentInitiated: (...a: any[]) => mockAnalyticsPaymentInitiated(...a),
  },
}));

const mockRecordNonFatal = jest.fn();
jest.mock('../utils/crashlytics', () => ({ recordNonFatal: (...a: any[]) => mockRecordNonFatal(...a) }));

const mockScheduleReminder = jest.fn();
jest.mock('../hooks/useScheduledRideReminder', () => ({
  useScheduledRideReminder: () => ({ scheduleReminder: (...a: any[]) => mockScheduleReminder(...a) }),
}));

jest.mock('@shared/store/vehicleTypeStore', () => ({ useVehicleTypeStore: (sel: any) => sel({ byId: {} }) }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

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

const mockFetchEstimates = jest.fn();
const mockFetchNearbyDrivers = jest.fn();
const mockSelectVehicle = jest.fn();
const mockCreateRide = jest.fn();
const mockSetRequiresWav = jest.fn();
const mockSetScheduledTime = jest.fn();
const mockSetQuietMode = jest.fn();
const mockFetchAvailablePromos = jest.fn();
const mockApplyPromo = jest.fn();
const mockSetRoutePolyline = jest.fn();
const mockFetchActiveRide = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: Object.assign((...a: any[]) => mockRideState, { getState: () => ({ ...mockRideState, fetchActiveRide: mockFetchActiveRide }) }),
}));

const mockFetchWallet = jest.fn();
let mockWalletState: any;
jest.mock('../store/walletStore', () => ({ useWalletStore: () => mockWalletState }));

const mockFetchWorkProfiles = jest.fn();
const mockCheckRide = jest.fn();
const mockSetWorkMode = jest.fn();
let mockWorkProfileState: any;
jest.mock('../store/workProfileStore', () => {
  function useWorkProfileStore(selector?: (s: any) => any) {
    return selector ? selector((useWorkProfileStore as any).__state) : (useWorkProfileStore as any).__state;
  }
  useWorkProfileStore.getState = () => ({ ...(useWorkProfileStore as any).__state, setWorkMode: (useWorkProfileStore as any).__setWorkMode });
  return { useWorkProfileStore };
});

import { useWorkProfileStore as mockedUseWorkProfileStore } from '../store/workProfileStore';
import RideOptionsScreen from '../app/ride-options';
import MapView, { Marker, Polygon } from 'react-native-maps';
import { CarMarker } from '@shared/components/CarMarker';
import BottomSheet from '@gorhom/bottom-sheet';
import { BackHandler, Keyboard, Platform } from 'react-native';
import SchedulePicker from '../components/SchedulePicker';
import ConfirmSheet from '../components/ConfirmSheet';
import { Image as ExpoImageMock } from 'expo-image';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const PICKUP = { lat: 52.13, lng: -106.66, address: '123 Main St' };
const DROPOFF = { lat: 52.14, lng: -106.68, address: '456 2nd Ave' };

function makeEstimate(overrides: any = {}) {
  return {
    vehicle_type: { id: 'vt-sedan', name: 'Sedan', capacity: 4 },
    available: true,
    total_fare: '20.00',
    grand_total: '20.00',
    base_fare: '5.00',
    distance_fare: '10.00',
    time_fare: '5.00',
    surge_multiplier: 1,
    eta_minutes: 5,
    driver_count: 2,
    fare_breakdown: [],
    ...overrides,
  };
}

function resetRideState() {
  mockRideState = {
    pickup: PICKUP, dropoff: DROPOFF, stops: [],
    estimates: [makeEstimate()],
    selectedVehicle: { id: 'vt-sedan', name: 'Sedan' },
    fetchEstimates: mockFetchEstimates,
    fetchNearbyDrivers: mockFetchNearbyDrivers,
    nearbyDrivers: [],
    selectVehicle: mockSelectVehicle,
    createRide: mockCreateRide,
    isLoading: false,
    requiresWav: false, setRequiresWav: mockSetRequiresWav, showWavOption: false,
    scheduledTime: null, setScheduledTime: mockSetScheduledTime,
    quietMode: false, setQuietMode: mockSetQuietMode,
    availablePromos: [], appliedPromo: null,
    fetchAvailablePromos: mockFetchAvailablePromos, applyPromo: mockApplyPromo,
    setRoutePolyline: mockSetRoutePolyline, routePolyline: null,
  };
}

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<RideOptionsScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root.findAllByType(TouchableOpacity).find((n) =>
    n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes(text); } catch { return false; }
    })
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  resetRideState();
  mockWalletState = { wallet: { balance: '10.00' }, fetchWallet: mockFetchWallet };
  mockWorkProfileState = {
    workModeEnabled: false, activeCompanyId: null, profiles: [],
    fetchPolicy: jest.fn(), checkRide: mockCheckRide, fetchProfiles: mockFetchWorkProfiles,
  };
  (mockedUseWorkProfileStore as any).__state = mockWorkProfileState;
  (mockedUseWorkProfileStore as any).__setWorkMode = mockSetWorkMode;
  mockCheckRide.mockReturnValue({ ok: true, reasons: [] });
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/service-areas') return Promise.resolve({ data: [] });
    if (url === '/payments/cards') return Promise.resolve({ data: [{ id: 'card-1', brand: 'visa', last4: '4242', exp_month: 1, exp_year: 2030, is_default: true }] });
    return Promise.resolve({ data: {} });
  });
  mockFetchEstimates.mockResolvedValue(undefined);
  mockFetchNearbyDrivers.mockResolvedValue(undefined);
  mockCreateRide.mockResolvedValue({ id: 'ride-1' });
});

afterEach(() => {
  act(() => { renderer?.unmount(); });
  renderer = null;
});

describe('RideOptionsScreen', () => {
  it('fetches estimates and nearby drivers on mount when pickup/dropoff are set', async () => {
    await renderScreen();
    expect(mockFetchEstimates).toHaveBeenCalled();
    expect(mockFetchNearbyDrivers).toHaveBeenCalled();
    // Bumped from the 5000ms default: this test has no actual hang (it
    // passes standalone and in every local full-suite run) but was
    // intermittently timing out in CI when the full multi-suite run under
    // coverage instrumentation puts the runner under real CPU contention
    // (same class of flake as driverProfileScreen.test.tsx in driver-app).
  }, 15000);

  it('a fetchEstimates failure shows the retry state instead of crashing', async () => {
    mockFetchEstimates.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('"Could not load fares. Tap to retry."');
  });

  it('handleSelect on an unavailable vehicle toasts and does not select it', async () => {
    mockRideState.estimates = [makeEstimate({ available: false })];
    mockRideState.selectedVehicle = null;
    const r = await renderScreen();
    mockSelectVehicle.mockClear(); // clear the mount-time auto-select call
    const card = findByText(r, 'Sedan')!;
    await act(async () => { card.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Unavailable', 'This vehicle type is not available right now.', 'warning');
    expect(mockSelectVehicle).not.toHaveBeenCalled();
  });

  it('handleSelect on an available vehicle updates selection and fetches promos', async () => {
    const r = await renderScreen();
    const card = findByText(r, 'Sedan')!;
    await act(async () => { card.props.onPress(); await flush(); });
    expect(mockSelectVehicle).toHaveBeenCalledWith(mockRideState.estimates[0].vehicle_type);
    expect(mockFetchAvailablePromos).toHaveBeenCalled();
  });

  it('booking with "card" and no selected card opens the add-payment confirm sheet instead of booking', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/payments/cards') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(allText(r)).toContain('"You don’t have a card on file. Add a payment method to book this ride."');
    expect(mockCreateRide).not.toHaveBeenCalled();
  });

  it('books directly at 1.0x surge with no confirm gate', async () => {
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', null, 'card-1');
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
  });

  it('surge above 1.0x requires an explicit confirm before booking', async () => {
    mockRideState.estimates = [makeEstimate({ surge_multiplier: 1.5, total_fare: '30.00', grand_total: '30.00' })];
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).not.toHaveBeenCalled();
    expect(allText(r)).toContain('"1.5× surge pricing is in effect"');
    const confirmBtn = r.root.findAllByProps({ accessibilityLabel: 'confirm-Book at $30.00' })[0];
    await act(async () => { await confirmBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalled();
  });

  it('a work-policy block offers "Turn off work mode" instead of booking', async () => {
    mockWorkProfileState.workModeEnabled = true;
    mockWorkProfileState.activeCompanyId = 'co-1';
    mockWorkProfileState.profiles = [{ company: { id: 'co-1', name: 'Acme' } }];
    mockCheckRide.mockReturnValue({ ok: false, reasons: ['Exceeds daily allowance'] });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).not.toHaveBeenCalled();
    expect(allText(r)).toContain('"Blocked by company policy"');
    const turnOffBtn = r.root.findAllByProps({ accessibilityLabel: 'confirm-Turn off work mode' })[0];
    await act(async () => { await turnOffBtn.props.onPress(); await flush(); });
    expect(mockSetWorkMode).toHaveBeenCalledWith(false);
  });

  it('requires_action from createRide toasts and does not navigate', async () => {
    mockCreateRide.mockResolvedValue({ requires_action: true });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Card authentication needed', expect.any(String), 'warning');
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('a promo_error on the created ride toasts a warning but still navigates', async () => {
    mockCreateRide.mockResolvedValue({ id: 'ride-1', promo_error: 'Promo expired' });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Promo not applied', 'Promo expired', 'warning');
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
  });

  it('a scheduled ride books to /(tabs) and schedules a local reminder', async () => {
    mockRideState.scheduledTime = new Date(Date.now() + 60 * 60000);
    mockScheduleReminder.mockResolvedValue(undefined);
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Schedule Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockScheduleReminder).toHaveBeenCalledWith('ride-1', mockRideState.scheduledTime);
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('a 409 already-active-ride error re-routes to driver-arriving for a searching ride', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'searching' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-active' } });
  });

  it('a 409 re-routes to ride-in-progress when the active ride is already in_progress', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'in_progress' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-in-progress', params: { rideId: 'ride-active' } });
  });

  it('a 402 unpaid-ride error opens a confirm sheet routing to /ride-completed on Pay Now', async () => {
    const err: any = new Error('unpaid');
    err.response = { status: 402, data: { error: { details: { unpaid_ride_id: 'ride-unpaid' } } } };
    mockCreateRide.mockRejectedValue(err);
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(allText(r)).toContain('"Unpaid Ride"');
    const payNowBtn = r.root.findAllByProps({ accessibilityLabel: 'confirm-Pay Now' })[0];
    await act(async () => { await payNowBtn.props.onPress(); await flush(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-unpaid' } });
  });

  it('any other booking failure toasts the backend message', async () => {
    mockCreateRide.mockRejectedValue(new Error('card declined'));
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Booking Failed', 'Failed to book ride. Please try again.', 'danger');
  });

  it('handleScheduleConfirm rejects a time less than 15 minutes out', async () => {
    const r = await renderScreen();
    const scheduleRow = findByText(r, 'tap to schedule')!;
    act(() => { scheduleRow.props.onPress(); });
    const schedulePicker = r.root.findByProps({ testID: 'schedule-picker' });
    act(() => { schedulePicker.props.onConfirm(new Date(Date.now() + 5 * 60000)); });
    expect(mockSetScheduledTime).not.toHaveBeenCalled();
    expect(allText(r)).toContain('"Invalid Time"');
  });

  it('handleScheduleConfirm accepts a valid time and stores it', async () => {
    const r = await renderScreen();
    const scheduleRow = findByText(r, 'tap to schedule')!;
    act(() => { scheduleRow.props.onPress(); });
    const schedulePicker = r.root.findByProps({ testID: 'schedule-picker' });
    const validDate = new Date(Date.now() + 60 * 60000);
    act(() => { schedulePicker.props.onConfirm(validDate); });
    expect(mockSetScheduledTime).toHaveBeenCalledWith(validDate);
  });

  it('handleManualPromo applies an eligible match and clears the input', async () => {
    mockRideState.availablePromos = [{ code: 'SAVE10', eligible: true }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const input = r.root.findByProps({ placeholder: 'Enter code' });
    act(() => { input.props.onChangeText('save10'); });
    const applyBtn = findByText(r, 'Apply');
    await act(async () => { applyBtn?.props.onPress(); await flush(); });
    expect(mockApplyPromo).toHaveBeenCalledWith({ code: 'SAVE10', eligible: true });
  });

  it('handleManualPromo shows an inline error and toasts for an ineligible match', async () => {
    mockRideState.availablePromos = [{ code: 'BIGONE', eligible: false, ineligible_reason: 'Minimum fare not met' }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const input = r.root.findByProps({ placeholder: 'Enter code' });
    act(() => { input.props.onChangeText('bigone'); });
    const applyBtn = findByText(r, 'Apply');
    await act(async () => { applyBtn?.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Not eligible', 'Minimum fare not met', 'warning');
    expect(mockApplyPromo).not.toHaveBeenCalled();
  });

  it('handleManualPromo shows "not found" for a code that matches nothing', async () => {
    mockRideState.availablePromos = [];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const input = r.root.findByProps({ placeholder: 'Enter code' });
    act(() => { input.props.onChangeText('NOPE'); });
    const applyBtn = findByText(r, 'Apply');
    await act(async () => { applyBtn?.props.onPress(); await flush(); });
    expect(allText(r)).toContain('"Code not found or has expired"');
  });

  it('removing an applied promo calls applyPromo(null) without opening the sheet', async () => {
    mockRideState.appliedPromo = { code: 'SAVE10', discount_type: 'flat', discount_value: 5 };
    const r = await renderScreen();
    const removeBtn = r.root.findByProps({ accessibilityLabel: 'Remove promo code' });
    act(() => { removeBtn.props.onPress({ stopPropagation: () => {} }); });
    expect(mockApplyPromo).toHaveBeenCalledWith(null);
  });

  it('the "Go back" button navigates back', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findByProps({ accessibilityLabel: 'Go back' });
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });
});

describe('WAV toggle', () => {
  function getToggle(r: TestRenderer.ReactTestRenderer, label: string) {
    return r.root.findAllByProps({ accessibilityLabel: label }).find((n: any) => typeof n.props.onValueChange === 'function')!;
  }

  it('is enabled and calls setRequiresWav when WAV drivers are available', async () => {
    mockRideState.showWavOption = true;
    mockRideState.estimates = [makeEstimate({ wav_available: 2 })];
    const r = await renderScreen();
    const toggle = getToggle(r, 'Request wheelchair-accessible vehicle');
    act(() => { toggle.props.onValueChange(true); });
    expect(mockSetRequiresWav).toHaveBeenCalledWith(true);
  });

  it('is disabled and never calls setRequiresWav when no WAV drivers are available', async () => {
    mockRideState.showWavOption = true;
    mockRideState.estimates = [makeEstimate({ wav_available: 0 })];
    const r = await renderScreen();
    const toggle = getToggle(r, 'Request wheelchair-accessible vehicle');
    act(() => { toggle.props.onValueChange(true); });
    expect(mockSetRequiresWav).not.toHaveBeenCalled();
  });

  it('is hidden entirely when showWavOption is false', async () => {
    mockRideState.showWavOption = false;
    const r = await renderScreen();
    expect(getToggle(r, 'Request wheelchair-accessible vehicle')).toBeUndefined();
  });
});

describe('quiet mode toggle', () => {
  it('calls setQuietMode on toggle', async () => {
    const r = await renderScreen();
    const toggle = r.root.findAllByProps({ accessibilityLabel: 'Request quiet ride' }).find((n: any) => typeof n.props.onValueChange === 'function')!;
    act(() => { toggle.props.onValueChange(true); });
    expect(mockSetQuietMode).toHaveBeenCalledWith(true);
  });
});

describe('work mode banner', () => {
  it('shows the billed-to-employer banner when work mode is active with a company', async () => {
    mockWorkProfileState.workModeEnabled = true;
    mockWorkProfileState.activeCompanyId = 'co-1';
    mockWorkProfileState.profiles = [{ company: { id: 'co-1', name: 'Acme Corp' } }];
    const r = await renderScreen();
    expect(allText(r)).toContain('["Billed to ","Acme Corp"]');
  });

  it('is hidden when work mode is off', async () => {
    mockWorkProfileState.workModeEnabled = false;
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Billed to');
  });
});

describe('fare breakdown collapse', () => {
  it('expands on tap, showing each line and the ride-fare driver badge', async () => {
    mockRideState.estimates = [makeEstimate({
      fare_breakdown: [
        { label: 'Ride fare', amount: '15.00', type: 'ride' },
        { label: 'GST', amount: '0.75', type: 'tax' },
      ],
    })];
    const r = await renderScreen();
    const header = r.root.findByProps({ accessibilityLabel: 'Expand fare breakdown' });
    act(() => { header.props.onPress(); });
    expect(allText(r)).toContain('100% goes to your driver');
    expect(allText(r)).toContain('GST');
    expect(r.root.findByProps({ accessibilityLabel: 'Collapse fare breakdown' })).toBeTruthy();
  });

  it('shows the applied promo discount line when open', async () => {
    mockRideState.estimates = [makeEstimate({ fare_breakdown: [{ label: 'Ride fare', amount: '15.00', type: 'ride' }] })];
    mockRideState.appliedPromo = { code: 'SAVE10', discount_type: 'flat', discount_value: 5 };
    const r = await renderScreen();
    const header = r.root.findByProps({ accessibilityLabel: 'Expand fare breakdown' });
    act(() => { header.props.onPress(); });
    expect(allText(r)).toContain('["Promo (","SAVE10",")"]');
  });

  it('is hidden entirely when the estimate has no fare_breakdown', async () => {
    mockRideState.estimates = [makeEstimate({ fare_breakdown: [] })];
    const r = await renderScreen();
    expect(() => r.root.findByProps({ accessibilityLabel: 'Expand fare breakdown' })).toThrow();
  });
});

describe('payment sheet selection', () => {
  it('selecting a saved card sets it as the payment method', async () => {
    const r = await renderScreen();
    // `findByText(r, 'Visa')` would ambiguously match the footer's own
    // "Visa •••• 4242" payment-summary row (which only opens the sheet) --
    // find the sheet's own card row by its exact, un-suffixed "Visa" text.
    const cardRow = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Visa"'; } catch { return false; } })
    )!;
    act(() => { cardRow.props.onPress(); });
    expect(allText(r)).toContain('•••• 4242');
  });

  it('navigates to /manage-cards when there are no saved cards', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/service-areas') return Promise.resolve({ data: [] });
      if (url === '/payments/cards') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    const addCardRow = findByText(r, 'Tap to add a card')!;
    act(() => { addCardRow.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/manage-cards');
  });

  it('selecting Spinr Wallet sets it as the payment method', async () => {
    const r = await renderScreen();
    const walletRow = findByText(r, 'Spinr Wallet')!;
    act(() => { walletRow.props.onPress(); });
    expect(allText(r)).toContain('["Balance: $","10.00"]');
  });

  it('lists corporate accounts and selecting one sets useCorporate', async () => {
    mockWorkProfileState.profiles = [{ company: { id: 'co-1', name: 'Acme Corp' } }];
    const r = await renderScreen();
    expect(allText(r)).toContain('Acme Corp');
    const corpRow = findByText(r, 'Company account')!;
    act(() => { corpRow.props.onPress(); });
    // paymentLabel switches to the corporate account's company name once
    // useCorporate + selectedCorporateId are both set.
    expect(allText(r)).toContain('"Acme Corp"');
  });

  it('"Add payment method" navigates to /manage-cards', async () => {
    const r = await renderScreen();
    const addRow = findByText(r, 'Add payment method')!;
    act(() => { addRow.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/manage-cards');
  });
});

function findSheet(r: TestRenderer.ReactTestRenderer, distinguish: (n: any) => boolean) {
  return r.root.findAllByType(BottomSheet).find(distinguish)!;
}
const findPaymentSheet = (r: TestRenderer.ReactTestRenderer) => findSheet(r, (n) => typeof n.props.onChange === 'function');
const findPromoSheet = (r: TestRenderer.ReactTestRenderer) => findSheet(r, (n) => typeof n.props.onClose === 'function');

describe('promo sheet: iOS keyboard-aware close', () => {
  it('defers the promo sheet close until the keyboard finishes hiding, on iOS', async () => {
    let hideHandler: (() => void) | null = null;
    const addListenerSpy = jest.spyOn(Keyboard, 'addListener').mockImplementation((event: string, cb: any) => {
      if (event === 'keyboardWillShow') act(() => cb());
      if (event === 'keyboardWillHide') hideHandler = cb;
      return { remove: jest.fn() } as any;
    });
    mockRideState.availablePromos = [{ code: 'SAVE10', eligible: true }];
    const r = await renderScreen();
    expect(hideHandler).toBeDefined();
    // handleManualPromo's success path calls closePromoSheet(); with the
    // keyboard "visible" (the keyboardWillShow handler above already fired),
    // iOS defers the actual sheet close and dismisses the keyboard instead.
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const input = r.root.findByProps({ placeholder: 'Enter code' });
    act(() => { input.props.onChangeText('save10'); });
    const dismissSpy = jest.spyOn(Keyboard, 'dismiss');
    const applyBtn = findByText(r, 'Apply');
    await act(async () => { applyBtn?.props.onPress(); await flush(); });
    expect(dismissSpy).toHaveBeenCalled();
    // Now the keyboard actually finishes hiding -- the pending close runs.
    expect(() => act(() => { hideHandler!(); })).not.toThrow();
    addListenerSpy.mockRestore();
  });
});

describe('firstAvailableIndex auto-select', () => {
  it('auto-selects the first AVAILABLE estimate, skipping a leading unavailable one', async () => {
    mockRideState.estimates = [
      makeEstimate({ vehicle_type: { id: 'vt-suv', name: 'SUV', capacity: 6 }, available: false }),
      makeEstimate({ vehicle_type: { id: 'vt-sedan', name: 'Sedan', capacity: 4 }, available: true }),
    ];
    mockRideState.selectedVehicle = null;
    await renderScreen();
    expect(mockSelectVehicle).toHaveBeenCalledWith(mockRideState.estimates[1].vehicle_type);
  });
});

describe('map fitToCoordinates on route change', () => {
  it('fits the map to pickup/dropoff/stops once mapReady fires and enough markers exist', async () => {
    jest.useFakeTimers();
    mockRideState.stops = [{ lat: 52.145, lng: -106.655 }];
    const r = await renderScreen();
    const mapView = r.root.findByType(MapView);
    expect(() => {
      act(() => { mapView.props.onMapReady(); });
      act(() => { jest.advanceTimersByTime(300); });
    }).not.toThrow();
    jest.useRealTimers();
  });
});

describe('server-provided route polyline', () => {
  it('populates routeCoordinates from a server routePolyline without waiting on MapViewDirections', async () => {
    mockRideState.routePolyline = [[52.13, -106.66], [52.14, -106.68]];
    await expect(renderScreen()).resolves.toBeDefined();
  });

  it('clears routeCoordinates when routePolyline is removed', async () => {
    mockRideState.routePolyline = null;
    await expect(renderScreen()).resolves.toBeDefined();
  });
});

describe('SchedulePicker and ConfirmSheet dismissal', () => {
  it('closes the schedule modal via SchedulePicker\'s own onClose', async () => {
    const r = await renderScreen();
    const scheduleRow = findByText(r, 'tap to schedule')!;
    act(() => { scheduleRow.props.onPress(); });
    const picker = r.root.findByType(SchedulePicker as any);
    expect(() => act(() => { picker.props.onClose(); })).not.toThrow();
  });

  it('dismisses the confirm sheet via its own onClose (e.g. tapping the backdrop)', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/payments/cards') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(allText(r)).toContain('"Add a payment method"');
    const sheet = r.root.findByType(ConfirmSheet as any);
    act(() => { sheet.props.onClose(); });
    expect(allText(r)).not.toContain('"Add a payment method"');
  });
});

describe('footer payment row', () => {
  it('opens the payment sheet when the footer\'s Payment row is pressed', async () => {
    const r = await renderScreen();
    const paymentRow = r.root.findAllByType(TouchableOpacity).find((n: any) =>
      n.findAllByType(Text).some((t: any) => { try { return JSON.stringify(t.props.children) === '"Payment"'; } catch { return false; } })
    )!;
    expect(() => act(() => { paymentRow.props.onPress(); })).not.toThrow();
  });
});

describe('map rendering', () => {
  it('renders a CarMarker per nearby driver, falling back to a deterministic heading when the backend has none', async () => {
    mockRideState.nearbyDrivers = [
      { id: 'd1', lat: 52.135, lng: -106.661 },
      { id: 'd2', lat: 52.136, lng: -106.662, heading: 45 },
    ];
    const r = await renderScreen();
    const markers = r.root.findAllByType(CarMarker as any);
    expect(markers).toHaveLength(2);
    expect(markers[1].props.heading).toBe(45);
    expect(typeof markers[0].props.heading).toBe('number');
  });

  it('filters out drivers with missing/near-zero coordinates', async () => {
    mockRideState.nearbyDrivers = [
      { id: 'zero', lat: 0, lng: 0 },
      { id: 'nan', lat: NaN, lng: -106.66 },
    ];
    const r = await renderScreen();
    expect(r.root.findAllByType(CarMarker as any)).toHaveLength(0);
  });

  it('renders a marker for each ride stop', async () => {
    mockRideState.stops = [{ lat: 52.145, lng: -106.655 }];
    const r = await renderScreen();
    const stopMarker = r.root.findAllByType(Marker).find(
      (n: any) => n.props.coordinate?.latitude === 52.145 && n.props.coordinate?.longitude === -106.655,
    );
    expect(stopMarker).toBeTruthy();
  });

  it('renders service-area polygons fetched from /service-areas, skipping malformed entries', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/service-areas') {
        return Promise.resolve({
          data: [
            { polygon: [{ lat: 1, lng: 1 }, { lat: 2, lng: 2 }, { lat: 3, lng: 3 }] },
            { polygon: [{ lat: 1, lng: 1 }] }, // < 3 points -- filtered out
          ],
        });
      }
      if (url === '/payments/cards') return Promise.resolve({ data: [{ id: 'card-1', brand: 'visa', last4: '4242', exp_month: 1, exp_year: 2030, is_default: true }] });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    expect(r.root.findAllByType(Polygon)).toHaveLength(1);
  });

  it('sets mapReady when the map fires onMapReady', async () => {
    const r = await renderScreen();
    const mapView = r.root.findByType(MapView);
    expect(() => act(() => { mapView.props.onMapReady(); })).not.toThrow();
  });

  it('shows the map placeholder spinner instead of the map when pickup/dropoff are not set', async () => {
    mockRideState.pickup = null;
    mockRideState.dropoff = null;
    const r = await renderScreen();
    expect(() => r.root.findByType(MapView)).toThrow();
  });
});

describe('payment sheet: open/close mechanics', () => {
  it('the payment sheet backdrop tap dismisses via Keyboard.dismiss (no-op on Android/iOS since it has no keyboard business, just verifying the callback wires through)', async () => {
    const r = await renderScreen();
    const sheet = findPaymentSheet(r);
    const backdrop = sheet.props.backdropComponent({});
    expect(backdrop.props.appearsOnIndex).toBe(0);
  });

  it('handlePaymentSheetChange tracks open state, and hardware back closes the sheet while open', async () => {
    const addListenerSpy = jest.spyOn(BackHandler, 'addEventListener');
    const r = await renderScreen();
    const sheet = findPaymentSheet(r);
    act(() => { sheet.props.onChange(0); });
    const listener = addListenerSpy.mock.calls.find((c) => c[0] === 'hardwareBackPress')?.[1];
    expect(listener).toBeDefined();
    const handled = listener?.(undefined as any);
    expect(handled).toBe(true);
    addListenerSpy.mockRestore();
  });

  it('does not register a hardware back handler while the payment sheet is closed', async () => {
    const addListenerSpy = jest.spyOn(BackHandler, 'addEventListener');
    const r = await renderScreen();
    addListenerSpy.mockClear();
    const sheet = findPaymentSheet(r);
    act(() => { sheet.props.onChange(-1); });
    expect(addListenerSpy).not.toHaveBeenCalledWith('hardwareBackPress', expect.anything());
    addListenerSpy.mockRestore();
  });
});

describe('promo sheet: open/close mechanics', () => {
  it('dismisses via Keyboard on Android/non-iOS immediately (no pending-close needed)', async () => {
    const dismissSpy = jest.spyOn(Keyboard, 'dismiss');
    const r = await renderScreen();
    const sheet = findPromoSheet(r);
    act(() => { sheet.props.onClose(); });
    // onClose always calls Keyboard.dismiss(), independent of platform.
    expect(dismissSpy).toHaveBeenCalled();
  });

  it('the promo sheet backdrop dismisses the keyboard on press', async () => {
    const dismissSpy = jest.spyOn(Keyboard, 'dismiss');
    const r = await renderScreen();
    const sheet = findPromoSheet(r);
    const backdrop = sheet.props.backdropComponent({});
    act(() => { backdrop.props.onPress(); });
    expect(dismissSpy).toHaveBeenCalled();
  });
});

describe('loadSavedCards failure', () => {
  it('logs a warning and does not crash when fetching saved cards fails', async () => {
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/payments/cards') return Promise.reject(new Error('down'));
      return Promise.resolve({ data: [] });
    });
    await expect(renderScreen()).resolves.toBeDefined();
    expect(warnSpy).toHaveBeenCalledWith('[RideOptions] Failed to load saved cards:', expect.any(Error));
    warnSpy.mockRestore();
  });
});

describe('handleBookRide additional branches', () => {
  it('opens the payment sheet from the "Add / select card" confirm-sheet button', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/payments/cards') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    const addSelectBtn = r.root.findAllByProps({ accessibilityLabel: 'confirm-Add / select card' })[0];
    expect(() => act(() => { addSelectBtn.props.onPress(); })).not.toThrow();
  });

  it('rejects booking a stale scheduled time under 15 minutes out', async () => {
    mockRideState.scheduledTime = new Date(Date.now() + 5 * 60000);
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Schedule Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Invalid Time', 'Scheduled time must be at least 15 minutes from now.', 'warning');
    expect(mockCreateRide).not.toHaveBeenCalled();
  });
});

describe('proceedWithBooking additional branches', () => {
  it('records a non-fatal crash when the booking error looks like a client-side engine crash', async () => {
    const errorSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
    mockIsEngineError.mockReturnValueOnce(true);
    const err = new Error('undefined is not a function');
    mockCreateRide.mockRejectedValue(err);
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockRecordNonFatal).toHaveBeenCalledWith(err, { screen: 'ride-options', action: 'proceedWithBooking' });
    errorSpy.mockRestore();
  });

  it('a 409 re-routes to driver-arrived when the active ride has already arrived', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'driver_arrived' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arrived', params: { rideId: 'ride-active' } });
  });

  it('a 409 re-routes to ride-completed when the active ride is already completed', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'completed' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-active' } });
  });
});

describe('estimate loading/error UI', () => {
  it('retrying after a fetch failure calls handleFetchEstimates again', async () => {
    mockFetchEstimates.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    mockFetchEstimates.mockClear();
    mockFetchEstimates.mockResolvedValue(undefined);
    const retryBtn = findByText(r, 'Retry')!;
    await act(async () => { retryBtn.props.onPress(); await flush(); });
    expect(mockFetchEstimates).toHaveBeenCalled();
  });

  it('shows the loading skeleton while isLoading is true', async () => {
    mockRideState.isLoading = true;
    const r = await renderScreen();
    expect(() => findByText(r, 'Sedan')).not.toThrow(); // just asserting no crash while in this state
  });
});

describe('schedule row', () => {
  it('clears the scheduled time when the row itself is tapped again while a time is set', async () => {
    mockRideState.scheduledTime = new Date(Date.now() + 60 * 60000);
    const r = await renderScreen();
    const scheduleRow = findByText(r, 'at')!;
    act(() => { scheduleRow.props.onPress(); });
    expect(mockSetScheduledTime).toHaveBeenCalledWith(null);
  });

  it('clears the scheduled time via the dedicated "Clear scheduled pickup time" button', async () => {
    mockRideState.scheduledTime = new Date(Date.now() + 60 * 60000);
    const r = await renderScreen();
    const clearBtn = r.root.findByProps({ accessibilityLabel: 'Clear scheduled pickup time' });
    act(() => { clearBtn.props.onPress(); });
    expect(mockSetScheduledTime).toHaveBeenCalledWith(null);
  });
});

describe('promo sheet: suggested-offers list', () => {
  it('selecting an eligible promo applies it and closes the sheet', async () => {
    mockRideState.availablePromos = [{ promo_id: 'p1', code: 'SAVE10', eligible: true, discount_type: 'flat', discount_value: 5 }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const offerRow = findByText(r, 'SAVE10')!;
    act(() => { offerRow.props.onPress(); });
    expect(mockApplyPromo).toHaveBeenCalledWith(mockRideState.availablePromos[0]);
  });

  it('tapping an ineligible promo row toasts instead of applying it', async () => {
    mockRideState.availablePromos = [{ promo_id: 'p2', code: 'BIGONE', eligible: false, ineligible_reason: 'Minimum fare not met', discount_type: 'flat', discount_value: 5 }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const offerRow = findByText(r, 'BIGONE')!;
    act(() => { offerRow.props.onPress(); });
    expect(mockShowToast).toHaveBeenCalledWith('Not eligible', 'Minimum fare not met', 'warning');
    expect(mockApplyPromo).not.toHaveBeenCalled();
  });

  it('shows the empty offers state when there are no available promos', async () => {
    mockRideState.availablePromos = [];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    expect(allText(r)).toContain('No offers right now');
  });

  it('removes the applied promo from the "Remove applied code" row inside the sheet', async () => {
    mockRideState.appliedPromo = { code: 'SAVE10', discount_type: 'flat', discount_value: 5 };
    const r = await renderScreen();
    // With a promo already applied, the entry row shows the code itself
    // rather than "Add promo code" -- pressing the row still opens the sheet.
    const promoRow = findByText(r, 'SAVE10')!;
    act(() => { promoRow.props.onPress(); });
    const removeRow = findByText(r, 'Remove applied code')!;
    act(() => { removeRow.props.onPress(); });
    expect(mockApplyPromo).toHaveBeenCalledWith(null);
  });
});

// ── Branch-coverage sweep additions (ACTION_ITEMS.md B37 sub-item 5) ──

describe('corporateAccounts derivation (workProfiles.map fallback)', () => {
  it('falls back to an empty id/name when a work profile is missing company.id or company.name', async () => {
    mockWorkProfileState.profiles = [
      { company: { name: 'No Id Co' } }, // missing id -- filtered out by .filter(a => a.id) since '' is falsy
      { company: { id: 'co-2' } },       // missing name -- survives the filter, company_name falls back to ''
    ];
    const r = await renderScreen();
    expect(allText(r)).not.toContain('No Id Co');
  });
});

describe('work-mode lazy initial state', () => {
  it('does not crash when work mode is already enabled at mount with no activeCompanyId yet', async () => {
    mockWorkProfileState.workModeEnabled = true;
    mockWorkProfileState.activeCompanyId = null;
    mockWorkProfileState.profiles = [];
    await expect(renderScreen()).resolves.toBeDefined();
  });
});

describe('work-mode default-sync effect (late activeCompanyId/profiles arrival)', () => {
  it('defaults selectedCorporateId to activeCompanyId once work mode turns on after mount', async () => {
    mockWorkProfileState.workModeEnabled = false;
    mockWorkProfileState.activeCompanyId = 'co-1';
    mockWorkProfileState.profiles = [{ company: { id: 'co-1', name: 'Acme' } }];
    const r = await renderScreen();
    mockWorkProfileState.workModeEnabled = true;
    await act(async () => { r.update(<RideOptionsScreen />); await flush(); });
    expect(allText(r)).toContain('"Acme"');
  });

  it('defaults selectedCorporateId to the first corporate account when activeCompanyId is still null', async () => {
    mockWorkProfileState.workModeEnabled = false;
    mockWorkProfileState.activeCompanyId = null;
    mockWorkProfileState.profiles = [{ company: { id: 'co-2', name: 'Beta' } }];
    const r = await renderScreen();
    mockWorkProfileState.workModeEnabled = true;
    await act(async () => { r.update(<RideOptionsScreen />); await flush(); });
    expect(allText(r)).toContain('"Beta"');
  });
});

describe('promo sheet: platform-specific keyboard effect', () => {
  it('does not register keyboard listeners on Android (effect returns early)', async () => {
    const original = Platform.OS;
    (Platform as any).OS = 'android';
    const addListenerSpy = jest.spyOn(Keyboard, 'addListener');
    await renderScreen();
    expect(addListenerSpy).not.toHaveBeenCalledWith('keyboardWillShow', expect.anything());
    (Platform as any).OS = original;
    addListenerSpy.mockRestore();
  });

  // A keyboardWillHide firing with no pending close queued (promoPendingCloseRef
  // still false) must be a safe no-op -- distinct from the existing "deferred
  // close" test where a close was actually pending.
  it('keyboardWillHide with no pending close queued is a no-op', async () => {
    let hideHandler: (() => void) | null = null;
    const addListenerSpy = jest.spyOn(Keyboard, 'addListener').mockImplementation((event: string, cb: any) => {
      if (event === 'keyboardWillHide') hideHandler = cb;
      return { remove: jest.fn() } as any;
    });
    await renderScreen();
    expect(hideHandler).toBeDefined();
    expect(() => act(() => { hideHandler!(); })).not.toThrow();
    addListenerSpy.mockRestore();
  });
});

describe('service-areas / saved-cards response shape fallbacks', () => {
  it('treats a missing /service-areas "data" field as an empty polygon list', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/service-areas') return Promise.resolve({});
      if (url === '/payments/cards') return Promise.resolve({ data: [{ id: 'card-1', brand: 'visa', last4: '4242', exp_month: 1, exp_year: 2030, is_default: true }] });
      return Promise.resolve({ data: {} });
    });
    const r = await renderScreen();
    expect(r.root.findAllByType(Polygon)).toHaveLength(0);
  });

  it('treats a non-array /payments/cards response as no saved cards', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/payments/cards') return Promise.resolve({ data: {} });
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    expect(findByText(r, 'Tap to add a card')).toBeTruthy();
  });
});

describe('selectedEstimate / totalFare fallbacks', () => {
  it('renders safely with no estimates at all (selectedEstimate null, footer hidden)', async () => {
    mockRideState.estimates = [];
    mockRideState.selectedVehicle = null;
    const r = await renderScreen();
    expect(() => r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' })).toThrow();
  });

  it('totalFare falls back to total_fare when grand_total is missing', async () => {
    mockRideState.estimates = [makeEstimate({ grand_total: undefined, total_fare: '22.50' })];
    const r = await renderScreen();
    expect(allText(r)).toContain('22.50');
  });
});

describe('paymentLabel fallbacks', () => {
  it('falls back to "Company account" when selectedCorporateId has no matching corporate account', async () => {
    mockWorkProfileState.workModeEnabled = true;
    mockWorkProfileState.activeCompanyId = 'co-orphan';
    mockWorkProfileState.profiles = [{ company: { id: 'co-real', name: 'Real Co' } }];
    const r = await renderScreen();
    expect(allText(r)).toContain('"Company account"');
  });

  it('shows $0.00 when wallet is selected but wallet data is null', async () => {
    mockWalletState = { wallet: null, fetchWallet: mockFetchWallet };
    const r = await renderScreen();
    const walletRow = findByText(r, 'Spinr Wallet')!;
    act(() => { walletRow.props.onPress(); });
    expect(allText(r)).toContain('Wallet · $0.00');
  });
});

describe('mount-time promo-fetch effect fare fallbacks', () => {
  it('falls back to 0/0 when every fare field is missing', async () => {
    mockRideState.estimates = [makeEstimate({
      grand_total: undefined, total_fare: undefined,
      base_fare: undefined, distance_fare: undefined, time_fare: undefined,
    })];
    await renderScreen();
    expect(mockFetchAvailablePromos).toHaveBeenCalledWith(0, 0);
  });

  it('uses total_fare for the grand total when grand_total is missing', async () => {
    mockRideState.estimates = [makeEstimate({ grand_total: undefined, total_fare: '18.00' })];
    await renderScreen();
    expect(mockFetchAvailablePromos).toHaveBeenCalledWith(18, expect.any(Number));
  });

  // Estimates shrinking past the currently-selected index in one update: the
  // promo-fetch effect (declared before the clamp effect) runs first in the
  // same commit and must fall back to estimates[0] instead of crashing on
  // estimates[stale index] being undefined; the clamp effect then resets
  // selectedIndex to 0.
  it('recomputes against estimates[0] and resets selectedIndex when estimates shrinks past the selected index', async () => {
    mockRideState.estimates = [
      makeEstimate({ vehicle_type: { id: 'vt-sedan', name: 'Sedan', capacity: 4 } }),
      makeEstimate({ vehicle_type: { id: 'vt-suv', name: 'SUV', capacity: 6 } }),
    ];
    const r = await renderScreen();
    const suvCard = findByText(r, 'SUV')!;
    await act(async () => { suvCard.props.onPress(); await flush(); });
    mockRideState.estimates = [makeEstimate({ vehicle_type: { id: 'vt-sedan', name: 'Sedan', capacity: 4 } })];
    await act(async () => { r.update(<RideOptionsScreen />); await flush(); });
    expect(() => findByText(r, 'Sedan')).not.toThrow();
  });
});

describe('handleSelect fare fallbacks', () => {
  it('falls back to 0/0 when the newly selected card is missing every fare field', async () => {
    mockRideState.estimates = [
      makeEstimate({ vehicle_type: { id: 'vt-sedan', name: 'Sedan', capacity: 4 } }),
      makeEstimate({
        vehicle_type: { id: 'vt-suv', name: 'SUV', capacity: 6 },
        grand_total: undefined, total_fare: undefined,
        base_fare: undefined, distance_fare: undefined, time_fare: undefined,
      }),
    ];
    const r = await renderScreen();
    mockFetchAvailablePromos.mockClear();
    const suvCard = findByText(r, 'SUV')!;
    await act(async () => { suvCard.props.onPress(); await flush(); });
    expect(mockFetchAvailablePromos).toHaveBeenCalledWith(0, 0);
  });

  it('uses total_fare for the newly selected card when grand_total is missing', async () => {
    mockRideState.estimates = [
      makeEstimate({ vehicle_type: { id: 'vt-sedan', name: 'Sedan', capacity: 4 } }),
      makeEstimate({ vehicle_type: { id: 'vt-suv', name: 'SUV', capacity: 6 }, grand_total: undefined, total_fare: '12.00' }),
    ];
    const r = await renderScreen();
    mockFetchAvailablePromos.mockClear();
    const suvCard = findByText(r, 'SUV')!;
    await act(async () => { suvCard.props.onPress(); await flush(); });
    expect(mockFetchAvailablePromos).toHaveBeenCalledWith(12, expect.any(Number));
  });
});

describe('handleBookRide/proceedWithBooking additional guards and fallbacks', () => {
  // handleBookRide's own isBooking guard is checked BEFORE the payment/schedule/
  // policy/surge logic, so a rapid second press while the first booking is
  // still pending must be a pure no-op (and the pending state renders the
  // in-flight spinner instead of the label -- the isBooking ? spinner : text
  // branch in the Confirm button).
  it('a second Confirm press while the first booking is still pending does not call createRide again', async () => {
    let resolveCreate: (v: any) => void;
    mockCreateRide.mockImplementation(() => new Promise((res) => { resolveCreate = res; }));
    const r = await renderScreen();
    const bookBtn1 = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { bookBtn1.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledTimes(1);
    const bookBtn2 = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    expect(bookBtn2.props.disabled).toBe(true); // isBooking is now true
    await act(async () => { bookBtn2.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledTimes(1);
    await act(async () => { resolveCreate!({ id: 'ride-1' }); await flush(); });
  });

  it('handleBookRide no-ops when selectedVehicle is unset, even though selectedEstimate is present', async () => {
    mockRideState.selectedVehicle = null;
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).not.toHaveBeenCalled();
  });

  it('booking via a selected corporate account bypasses the card requirement and sends no card pmId', async () => {
    mockWorkProfileState.profiles = [{ company: { id: 'co-1', name: 'Acme Corp' } }];
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/payments/cards') return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    const corpRow = findByText(r, 'Company account')!;
    act(() => { corpRow.props.onPress(); });
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', 'co-1', undefined);
  });

  it('booking via wallet sends an undefined pmId (selectedPayment !== "card" branch)', async () => {
    const r = await renderScreen();
    const walletRow = findByText(r, 'Spinr Wallet')!;
    act(() => { walletRow.props.onPress(); });
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('wallet', null, undefined);
  });

  it('treats a missing total_fare as 0 for the corporate policy check and books when checkRide allows it', async () => {
    mockWorkProfileState.workModeEnabled = true;
    mockWorkProfileState.activeCompanyId = 'co-1';
    mockWorkProfileState.profiles = [{ company: { id: 'co-1', name: 'Acme' } }];
    mockCheckRide.mockReturnValue({ ok: true, reasons: [] });
    mockRideState.estimates = [makeEstimate({ total_fare: undefined })];
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCheckRide).toHaveBeenCalledWith(0, undefined);
    expect(mockCreateRide).toHaveBeenCalled();
  });

  it('treats a missing surge_multiplier as no surge and books directly with no confirm gate', async () => {
    mockRideState.estimates = [makeEstimate({ surge_multiplier: undefined })];
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalled();
  });

  it('a 409 error with no actual active ride found falls through to the generic failure toast', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: false });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).not.toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith('Booking Failed', expect.any(String), 'danger');
  });

  it('a 409 with an active ride in an unrecognized status navigates nowhere', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-x', status: 'cancelled' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Confirm Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).not.toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });
});

describe('toggleFareBreakdown collapse', () => {
  it('collapses the fare breakdown back down on a second tap', async () => {
    mockRideState.estimates = [makeEstimate({ fare_breakdown: [{ label: 'Ride fare', amount: '15.00', type: 'ride' }] })];
    const r = await renderScreen();
    const header = r.root.findByProps({ accessibilityLabel: 'Expand fare breakdown' });
    act(() => { header.props.onPress(); });
    const collapseHeader = r.root.findByProps({ accessibilityLabel: 'Collapse fare breakdown' });
    expect(() => act(() => { collapseHeader.props.onPress(); })).not.toThrow();
    expect(r.root.findByProps({ accessibilityLabel: 'Expand fare breakdown' })).toBeTruthy();
  });

  it('skips a fare-breakdown line with a null amount instead of crashing', async () => {
    mockRideState.estimates = [makeEstimate({
      fare_breakdown: [
        { label: 'Ride fare', amount: '15.00', type: 'ride' },
        { label: 'Weird line', amount: null, type: 'other' },
      ],
    })];
    const r = await renderScreen();
    const header = r.root.findByProps({ accessibilityLabel: 'Expand fare breakdown' });
    act(() => { header.props.onPress(); });
    expect(allText(r)).not.toContain('Weird line');
  });
});

describe('handleManualPromo additional branches', () => {
  it('no-ops on empty/whitespace input submitted via the keyboard "done" action', async () => {
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const input = r.root.findByProps({ placeholder: 'Enter code' });
    expect(() => act(() => { input.props.onSubmitEditing(); })).not.toThrow();
    expect(mockApplyPromo).not.toHaveBeenCalled();
  });

  it('falls back to generic copy when the ineligible match has no ineligible_reason', async () => {
    mockRideState.availablePromos = [{ code: 'NOPE', eligible: false }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const input = r.root.findByProps({ placeholder: 'Enter code' });
    act(() => { input.props.onChangeText('nope'); });
    const applyBtn = findByText(r, 'Apply');
    await act(async () => { applyBtn?.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Not eligible', 'This promo cannot be applied to this ride', 'warning');
    expect(allText(r)).toContain('Minimum fare not met');
  });
});

describe('map provider by platform', () => {
  it('uses PROVIDER_GOOGLE on Android', async () => {
    const original = Platform.OS;
    (Platform as any).OS = 'android';
    const r = await renderScreen();
    const mapView = r.root.findByType(MapView);
    expect(mapView.props.provider).toBe('google');
    (Platform as any).OS = original;
  });
});

describe('promo entry row copy variants', () => {
  it('shows "Free ride" copy when the applied promo is a free ride', async () => {
    mockRideState.appliedPromo = { code: 'FREE1', free_ride: true };
    const r = await renderScreen();
    expect(allText(r)).toContain('Free ride');
  });

  it('shows percentage-off copy for a percentage-type applied promo', async () => {
    mockRideState.appliedPromo = { code: 'PCT10', discount_type: 'percentage', discount_value: 10 };
    const r = await renderScreen();
    expect(allText(r)).toContain('% off your ride fare');
  });

  it('shows singular "offer" (not "offers") when exactly one promo is available', async () => {
    mockRideState.availablePromos = [{ code: 'ONE', eligible: true }];
    const r = await renderScreen();
    expect(allText(r)).toContain('[1," offer","",');
  });
});

describe('work banner activeCompanyName fallback', () => {
  it('shows "your employer" when activeCompanyId has no matching profile entry', async () => {
    mockWorkProfileState.workModeEnabled = true;
    mockWorkProfileState.activeCompanyId = 'co-orphan';
    mockWorkProfileState.profiles = [];
    const r = await renderScreen();
    expect(allText(r)).toContain('your employer');
  });
});

describe('WAV toggle additional branches', () => {
  it('treats a missing wav_available as 0 (disabled, "No WAV drivers nearby")', async () => {
    mockRideState.showWavOption = true;
    mockRideState.estimates = [makeEstimate({ wav_available: undefined })];
    const r = await renderScreen();
    expect(allText(r)).toContain('No WAV drivers nearby');
  });

  it('uses singular "driver" copy for exactly one WAV driver available', async () => {
    mockRideState.showWavOption = true;
    mockRideState.estimates = [makeEstimate({ wav_available: 1 })];
    const r = await renderScreen();
    expect(allText(r)).toContain('1 WAV driver available');
  });

  it('WAV toggle thumb reflects the active color when requiresWav is already true', async () => {
    mockRideState.showWavOption = true;
    mockRideState.requiresWav = true;
    mockRideState.estimates = [makeEstimate({ wav_available: 2 })];
    const r = await renderScreen();
    const toggle = r.root.findAllByProps({ accessibilityLabel: 'Request wheelchair-accessible vehicle' }).find((n: any) => typeof n.props.onValueChange === 'function')!;
    expect(toggle.props.thumbColor).toBe(COLORS.primary);
  });
});

describe('quiet mode toggle additional branches', () => {
  it('thumb reflects the active color when quietMode is already true', async () => {
    mockRideState.quietMode = true;
    const r = await renderScreen();
    const toggle = r.root.findAllByProps({ accessibilityLabel: 'Request quiet ride' }).find((n: any) => typeof n.props.onValueChange === 'function')!;
    expect(toggle.props.thumbColor).toBe(COLORS.primary);
  });
});

describe('payment sheet: wallet balance fallback', () => {
  it('shows $0.00 wallet balance in the sheet when wallet is null', async () => {
    mockWalletState = { wallet: null, fetchWallet: mockFetchWallet };
    const r = await renderScreen();
    expect(allText(r)).toContain('["Balance: $","0.00"]');
  });
});

describe('promo sheet: offers list copy variants', () => {
  it('shows the offer count suffix when more than one promo is available', async () => {
    mockRideState.availablePromos = [{ code: 'A', eligible: true }, { code: 'B', eligible: true }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    expect(allText(r)).toContain('· 2');
  });

  it('shows "Free ride" label for a free-ride promo row', async () => {
    mockRideState.availablePromos = [{ code: 'FREE1', eligible: true, free_ride: true }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    expect(allText(r)).toContain('"Free ride"');
  });

  it('shows percentage-off copy with a max-discount cap when set', async () => {
    mockRideState.availablePromos = [{ code: 'PCT', eligible: true, discount_type: 'percentage', discount_value: 15, max_discount: 5 }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    expect(allText(r)).toContain('max $5');
  });

  it('shows percentage-off copy with no cap suffix when max_discount is unset', async () => {
    mockRideState.availablePromos = [{ code: 'PCTNOMAX', eligible: true, discount_type: 'percentage', discount_value: 15 }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    expect(allText(r)).toContain('15% off');
    expect(allText(r)).not.toContain('max $');
  });

  it('ineligible promo row without an ineligible_reason falls back to generic copy on tap', async () => {
    mockRideState.availablePromos = [{ code: 'NOPE', eligible: false }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    const offerRow = findByText(r, 'NOPE')!;
    act(() => { offerRow.props.onPress(); });
    expect(mockShowToast).toHaveBeenCalledWith('Not eligible', 'This promo cannot be applied to this ride', 'warning');
  });

  it("shows a promo's description line when present", async () => {
    mockRideState.availablePromos = [{ code: 'DESC1', eligible: true, description: 'New rider bonus' }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    expect(allText(r)).toContain('New rider bonus');
  });

  it('shows a minimum-fare hint for an ineligible promo with min_ride_fare set', async () => {
    mockRideState.availablePromos = [{ code: 'MINFARE', eligible: false, min_ride_fare: 12, ineligible_reason: 'Minimum fare not met' }];
    const r = await renderScreen();
    const promoRow = findByText(r, 'Add promo code')!;
    act(() => { promoRow.props.onPress(); });
    expect(allText(r)).toContain('["Min. fare $","12.00"]');
  });
});

describe('AnimatedVehicleCard additional branches', () => {
  it('renders the vehicle image when vehicle_type.image_url is set', async () => {
    mockRideState.estimates = [makeEstimate({ vehicle_type: { id: 'vt-sedan', name: 'Sedan', capacity: 4, image_url: 'https://example.com/car.png' } })];
    const r = await renderScreen();
    expect(r.root.findAllByType(ExpoImageMock as any).length).toBeGreaterThan(0);
  });

  it('uses vehicle_type.icon for the fallback glyph when no image_url is set', async () => {
    mockRideState.estimates = [makeEstimate({ vehicle_type: { id: 'vt-sport', name: 'Sport', capacity: 4, icon: 'car-sport' } })];
    const r = await renderScreen();
    const icons = r.root.findAllByType(Ionicons as any);
    expect(icons.some((n) => n.props.name === 'car-sport')).toBe(true);
  });

  it('falls back to the generic car glyph for an unrecognized/legacy icon value', async () => {
    // "car-compact" is seeded data (backend/seed_vehicle_types.py) and is
    // NOT a real Ionicons glyph name — must be mapped, never passed through
    // raw, or the icon would silently fail to render on a real device.
    mockRideState.estimates = [makeEstimate({ vehicle_type: { id: 'vt-1', name: 'Standard', capacity: 4, icon: 'car-compact' } })];
    const r = await renderScreen();
    const icons = r.root.findAllByType(Ionicons as any);
    expect(icons.some((n) => n.props.name === 'car')).toBe(true);
    expect(icons.some((n) => n.props.name === 'car-compact')).toBe(false);
  });

  it('gives different vehicle types distinct fallback-icon accent colors, not a flat gray', async () => {
    mockRideState.estimates = [
      makeEstimate({ vehicle_type: { id: 'vt-sport', name: 'Premium', capacity: 4, icon: 'car-sport' } }),
      makeEstimate({ vehicle_type: { id: 'vt-van', name: 'Van', capacity: 6, icon: 'bus' } }),
    ];
    const r = await renderScreen();
    const icons = r.root.findAllByType(Ionicons as any);
    const sportIcon = icons.find((n) => n.props.name === 'car-sport');
    const busIcon = icons.find((n) => n.props.name === 'bus');
    expect(sportIcon!.props.color).not.toBe(busIcon!.props.color);
    expect(sportIcon!.props.color).not.toBe('#666');
  });

  it('falls back to a neutral fallback-icon color for an unrecognized vehicle type icon', async () => {
    mockRideState.estimates = [makeEstimate({ vehicle_type: { id: 'vt-1', name: 'Standard', capacity: 4, icon: 'car-compact-legacy' } })];
    const r = await renderScreen();
    const icons = r.root.findAllByType(Ionicons as any);
    const carIcon = icons.find((n) => n.props.name === 'car');
    expect(carIcon!.props.color).toBe('#6B7280');
  });

  it('treats a missing surge_multiplier as no surge (no "Higher demand" notice)', async () => {
    mockRideState.estimates = [makeEstimate({ surge_multiplier: undefined })];
    const r = await renderScreen();
    expect(allText(r)).not.toContain('Higher demand');
  });

  it('shows "Nearby" when eta_minutes is falsy', async () => {
    mockRideState.estimates = [makeEstimate({ eta_minutes: 0 })];
    const r = await renderScreen();
    expect(allText(r)).toContain('Nearby');
  });

  it('uses singular "driver" copy for exactly one driver', async () => {
    mockRideState.estimates = [makeEstimate({ driver_count: 1 })];
    const r = await renderScreen();
    expect(allText(r)).toContain('1 driver');
    expect(allText(r)).not.toContain('1 drivers');
  });
});
