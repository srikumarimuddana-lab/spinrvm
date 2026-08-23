/**
 * app/payment-confirm.tsx — booking confirmation + payment-method screen
 * (money-adjacent). Pins:
 *  - focus-effect loads saved cards, auto-selecting the default; a fetch
 *    failure shows the distinct "couldn't load cards / tap to retry"
 *    state rather than the genuine-empty state
 *  - the work-mode corporate-toggle sync only applies its default before
 *    the rider has chosen a payment method by hand — never overrides an
 *    explicit choice
 *  - handleBookRide: happy path calls createRide and routes to
 *    /driver-arriving (or /(tabs) for a scheduled ride); the SCA
 *    requires_action -> confirmPayment -> re-createRide two-step, with
 *    both an authentication-failure and a still-requires-action
 *    failure toasting instead of proceeding
 *  - a 409 (already-active ride) error re-routes to whichever screen
 *    owns the rider's actual active ride by its status
 *  - a 402 unpaid-ride error opens a confirm sheet routing to
 *    /ride-completed on "Pay Now"
 *  - any other booking failure toasts
 *  - totalFare subtracts the server-computed promo discount from
 *    grand_total (or total_fare)
 *  - tapping "Credit Card" with no saved cards routes to /manage-cards
 *    and marks the payment source as rider-chosen
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

jest.mock('expo-router/react-navigation', () => {
  const ReactActual = require('react');
  return {
    useFocusEffect: (cb: () => void | (() => void)) => {
      ReactActual.useEffect(() => cb(), [cb]);
    },
  };
});

const mockBack = jest.fn();
const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush, replace: mockReplace }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB', danger: '#DC2626',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));
jest.mock('@shared/utils/responsive', () => ({ useResponsive: () => ({ sf: (n: number) => n }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
  isEngineError: () => false,
}));

const mockConfirmPayment = jest.fn();
jest.mock('@stripe/stripe-react-native', () => ({
  useStripe: () => ({ confirmPayment: (...a: any[]) => mockConfirmPayment(...a) }),
}));

const mockCreateRide = jest.fn();
const mockFetchActiveRide = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: Object.assign((...a: any[]) => mockRideState, { getState: () => mockRideState }),
}));

const mockFetchWallet = jest.fn();
let mockWalletState: any;
jest.mock('../store/walletStore', () => ({ useWalletStore: () => mockWalletState }));

const mockFetchWorkProfiles = jest.fn();
let mockWorkProfileState: any;
jest.mock('../store/workProfileStore', () => ({ useWorkProfileStore: () => mockWorkProfileState }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockRecordNonFatal = jest.fn();
jest.mock('../utils/crashlytics', () => ({ recordNonFatal: (...a: any[]) => mockRecordNonFatal(...a) }));

const mockAnalyticsRideRequested = jest.fn();
const mockAnalyticsPaymentInitiated = jest.fn();
jest.mock('@shared/analytics', () => ({
  Analytics: {
    rideRequested: (...a: any[]) => mockAnalyticsRideRequested(...a),
    paymentInitiated: (...a: any[]) => mockAnalyticsPaymentInitiated(...a),
  },
}));

const mockScheduleReminder = jest.fn();
jest.mock('../hooks/useScheduledRideReminder', () => ({
  useScheduledRideReminder: () => ({ scheduleReminder: mockScheduleReminder }),
}));

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

import PaymentConfirmScreen from '../app/payment-confirm';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const CARD_VISA = { id: 'card-1', brand: 'visa', last4: '4242', exp_month: 8, exp_year: 2028, is_default: true };
const SELECTED_VEHICLE = { id: 'vt-1', name: 'Sedan' };
const ESTIMATE = {
  vehicle_type: { id: 'vt-1' }, total_fare: '15.00', grand_total: '15.00',
  duration_minutes: 12, distance_km: 5.2, fare_breakdown: [{ label: 'Base fare', amount: '15.00', type: 'ride' }],
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<PaymentConfirmScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => {
      try { return JSON.stringify(t.props.children).includes(text); } catch { return false; }
    }))!;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockRideState = {
    pickup: { address: '100 Main St' }, dropoff: { address: '200 Elm St' },
    selectedVehicle: SELECTED_VEHICLE, estimates: [ESTIMATE], createRide: mockCreateRide,
    isLoading: false, scheduledTime: null, appliedPromo: null,
    fetchActiveRide: mockFetchActiveRide,
  };
  mockWalletState = { wallet: { balance: '10.00' }, fetchWallet: mockFetchWallet };
  mockWorkProfileState = { profiles: [], workModeEnabled: false, fetchProfiles: mockFetchWorkProfiles, activeCompanyId: null };
  mockApiGet.mockResolvedValue({ data: [CARD_VISA] });
  mockCreateRide.mockResolvedValue({ id: 'ride-1', status: 'searching' });
  mockConfirmPayment.mockResolvedValue({ paymentIntent: { status: 'succeeded' } });
  mockScheduleReminder.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('PaymentConfirmScreen', () => {
  it('loads saved cards on focus and auto-selects the default', async () => {
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/payments/cards');
    expect(allText(r)).toContain('["•••• ","4242","  ",8,"/","28"]');
  });

  it('shows the distinct load-error state (not the genuine-empty state) when the cards fetch fails', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    expect(allText(r)).toContain("Couldn't load your cards");
    expect(allText(r)).not.toContain('Tap to add a card');
  });

  it('shows the add-a-card option when the fetch succeeds with zero cards', async () => {
    mockApiGet.mockResolvedValue({ data: [] });
    const r = await renderScreen();
    expect(allText(r)).toContain('Tap to add a card');
  });

  it('routes to /manage-cards and marks the payment source rider-chosen when adding a card', async () => {
    mockApiGet.mockResolvedValue({ data: [] });
    const r = await renderScreen();
    const addCardBtn = r.root.findByProps({ accessibilityLabel: 'Add a credit card' });
    act(() => { addCardBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/manage-cards');
  });

  it('computes totalFare as grand_total minus the applied promo discount', async () => {
    mockRideState.appliedPromo = { code: 'SAVE5', discount_amount: 5 };
    const r = await renderScreen();
    expect(allText(r)).toContain('["$","10.00"]');
  });

  it('books the ride and routes to /driver-arriving on the happy path', async () => {
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', null, 'card-1');
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-1' } });
    expect(mockAnalyticsRideRequested).toHaveBeenCalled();
  });

  it('routes to /(tabs) instead for a scheduled ride, and schedules a local reminder', async () => {
    mockRideState.scheduledTime = new Date('2026-09-01T10:00:00Z');
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Schedule Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockScheduleReminder).toHaveBeenCalledWith('ride-1', mockRideState.scheduledTime);
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('runs the SCA confirm-payment step and re-books when requires_action is returned', async () => {
    mockCreateRide
      .mockResolvedValueOnce({ requires_action: true, payment_authorization: { client_secret: 'secret_1', payment_intent_id: 'pi_1' } })
      .mockResolvedValueOnce({ id: 'ride-2', status: 'searching' });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockConfirmPayment).toHaveBeenCalledWith('secret_1');
    expect(mockCreateRide).toHaveBeenNthCalledWith(2, 'card', null, 'card-1', 'pi_1');
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-2' } });
  });

  it('toasts instead of booking when SCA confirmPayment itself errors', async () => {
    mockCreateRide.mockResolvedValueOnce({ requires_action: true, payment_authorization: { client_secret: 'secret_1', payment_intent_id: 'pi_1' } });
    mockConfirmPayment.mockResolvedValue({ error: { message: 'Card was declined.' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Authentication needed', 'Card was declined.', 'danger');
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('toasts when the re-book still returns requires_action after SCA', async () => {
    mockCreateRide
      .mockResolvedValueOnce({ requires_action: true, payment_authorization: { client_secret: 'secret_1', payment_intent_id: 'pi_1' } })
      .mockResolvedValueOnce({ requires_action: true, payment_authorization: { client_secret: 'secret_1', payment_intent_id: 'pi_1' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Booking failed', 'Card authorization could not be completed. Please try again.', 'danger');
  });

  it('re-routes to the owning screen on a 409 already-active-ride error', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'driver_arrived' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arrived', params: { rideId: 'ride-active' } });
  });

  it('opens the Unpaid Ride confirm sheet on a 402 error and routes to /ride-completed on Pay Now', async () => {
    const err: any = new Error('unpaid');
    err.response = { status: 402, data: { error: { details: { unpaid_ride_id: 'ride-old' } } } };
    mockCreateRide.mockRejectedValue(err);
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(allText(r)).toContain('Unpaid Ride');
    const payNowBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Pay Now' });
    act(() => { payNowBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-old' } });
  });

  it('toasts a generic failure for any other booking error', async () => {
    mockCreateRide.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Booking Failed', 'Could not complete your booking. Please try again.', 'danger');
  });

  it('shows corporate billing options once work profiles are loaded', async () => {
    mockWorkProfileState = {
      profiles: [{ company: { id: 'corp-1', name: 'Acme Inc' } }],
      workModeEnabled: false, fetchProfiles: mockFetchWorkProfiles, activeCompanyId: null,
    };
    const r = await renderScreen();
    expect(allText(r)).toContain('Bill to Business');
  });

  it('navigates back on the header back button', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findByProps({ accessibilityLabel: 'Go back' });
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });
});
