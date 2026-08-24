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

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));
jest.mock('@shared/utils/responsive', () => ({ useResponsive: () => ({ sf: (n: number) => n }) }));

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
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
  isEngineError: () => false,
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
    const cardRow = findByText(r, 'Visa')!;
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
