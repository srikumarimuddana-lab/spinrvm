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
 *
 * Three branches are deliberately left uncovered (all genuinely
 * unreachable, not chased):
 *  - the work-mode default effect's final `?? null` fallback in
 *    `prev ?? activeCompanyId ?? firstCorporateAccountId ?? null` — that
 *    effect only ever runs when `corporateAccounts.length > 0`, which
 *    guarantees `firstCorporateAccountId` is truthy, so the chain can
 *    never fall through to the literal `null`.
 *  - the fare-breakdown value's `line.amount != null ? ... : 'Applied'`
 *    ternary's false branch — dead code: the enclosing `.map()` already
 *    requires `line.amount != null` to render this row at all (the
 *    identical check one level up), so by the time this second check
 *    runs it can only ever be true.
 *  - `createStyles`'s `sf` default parameter — never hit because every
 *    real call site passes `sf` from `useResponsive()`, and the mock
 *    here always provides one too.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, ActivityIndicator } from 'react-native';

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
const mockIsEngineError = jest.fn(() => false);
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
  isEngineError: (..._a: any[]) => mockIsEngineError(),
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
  mockIsEngineError.mockReset().mockReturnValue(false);
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

  it('selects a different saved card', async () => {
    const CARD_MC = { id: 'card-2', brand: 'mastercard', last4: '9999', exp_month: 1, exp_year: 2030, is_default: false };
    mockApiGet.mockResolvedValue({ data: [CARD_VISA, CARD_MC] });
    const r = await renderScreen();
    const mcRow = r.root.findByProps({ accessibilityLabel: 'Mastercard card ending in 9999, expires 1/30' });
    act(() => { mcRow.props.onPress(); });
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', null, 'card-2');
  });

  it('selects wallet as the payment method', async () => {
    const r = await renderScreen();
    const walletRow = r.root.findByProps({ accessibilityLabel: 'Spinr Wallet' });
    act(() => { walletRow.props.onPress(); });
    expect(allText(r)).toContain('["Balance: $","10.00"]');
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('wallet', null, undefined);
  });

  it('navigates to /manage-cards from "Add Payment Method"', async () => {
    const r = await renderScreen();
    const addBtn = r.root.findByProps({ accessibilityLabel: 'Add payment method' });
    act(() => { addBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/manage-cards');
  });

  it('BUG (found, not fixed — see ACTION_ITEMS.md): manually toggling "Bill to Business" on with a single corporate account never sets selectedCorporateId, so the ride silently books to the personal card', async () => {
    // The toggle's onValueChange only calls setUseCorporate(v) — it never
    // sets selectedCorporateId. That only gets auto-filled by the
    // work-mode-default effect (shouldApplyWorkModeDefault), which requires
    // workModeEnabled === true. A rider who is NOT in work mode but has one
    // corporate account, and manually flips the toggle on, ends up with
    // useCorporate=true but selectedCorporateId=null — corpId then
    // evaluates to null in handleBookRide, and the subtitle keeps showing
    // "Use a corporate account" instead of the company name (the toggle
    // *looks* on but nothing is actually selected). The rider is billed
    // personally while believing they billed their employer. The picker
    // that would let them pick manually only renders when there are 2+
    // accounts, so a single-account rider has no way to complete the
    // selection at all outside of work mode.
    mockWorkProfileState = {
      profiles: [{ company: { id: 'corp-1', name: 'Acme Inc' } }],
      workModeEnabled: false, fetchProfiles: mockFetchWorkProfiles, activeCompanyId: null,
    };
    const r = await renderScreen();
    const toggle = r.root.findAllByProps({ accessibilityLabel: 'Bill to business' }).find((n) => typeof n.props.onPress === 'function')!;
    act(() => { toggle.props.onPress(); });
    expect(allText(r)).toContain('Use a corporate account'); // never switches to "Acme Inc"
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', null, 'card-1'); // bills personally, not corp-1
  });

  it('shows a picker with 2+ corporate accounts and selects one', async () => {
    mockWorkProfileState = {
      profiles: [{ company: { id: 'corp-1', name: 'Acme Inc' } }, { company: { id: 'corp-2', name: 'Beta LLC' } }],
      workModeEnabled: false, fetchProfiles: mockFetchWorkProfiles, activeCompanyId: null,
    };
    const r = await renderScreen();
    const toggle = r.root.findAllByProps({ accessibilityLabel: 'Bill to business' }).find((n) => typeof n.props.onPress === 'function')!;
    act(() => { toggle.props.onPress(); });
    const betaOption = r.root.findByProps({ accessibilityLabel: 'Beta LLC' });
    act(() => { betaOption.props.onPress(); });
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', 'corp-2', 'card-1');
  });

  it('defaults corporate billing on for a work-mode rider who has not chosen a payment method yet', async () => {
    mockWorkProfileState = {
      profiles: [{ company: { id: 'corp-1', name: 'Acme Inc' } }],
      workModeEnabled: true, fetchProfiles: mockFetchWorkProfiles, activeCompanyId: 'corp-1',
    };
    const r = await renderScreen();
    expect(allText(r)).toContain('Acme Inc');
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', 'corp-1', 'card-1');
  });

  it('expands the fare breakdown, showing line items and the promo discount line', async () => {
    mockRideState.appliedPromo = { code: 'SAVE5', discount_amount: 5 };
    const r = await renderScreen();
    const fareHeader = findButtonByText(r, 'View fare details');
    act(() => { fareHeader.props.onPress(); });
    expect(allText(r)).toContain('Hide details');
    expect(allText(r)).toContain('Base fare');
    expect(allText(r)).toContain('["Promo (","SAVE5",")"]');
  });

  it('collapses the fare breakdown on a second tap', async () => {
    const r = await renderScreen();
    const fareHeader = findButtonByText(r, 'View fare details');
    act(() => { fareHeader.props.onPress(); });
    act(() => { findButtonByText(r, 'Hide details').props.onPress(); });
    expect(allText(r)).toContain('View fare details');
  });

  it('shows the scheduled-ride badge with a formatted date/time', async () => {
    mockRideState.scheduledTime = new Date('2026-09-01T15:00:00Z');
    const r = await renderScreen();
    expect(allText(r)).toContain('Scheduled:');
  });

  it('toasts "Promo not applied" when the booked ride carries a promo_error', async () => {
    mockCreateRide.mockResolvedValue({ id: 'ride-1', status: 'searching', promo_error: 'Promo code expired' });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Promo not applied', 'Promo code expired', 'warning');
  });

  it('toasts when SCA confirms but the payment intent status is neither requires_capture nor succeeded', async () => {
    mockCreateRide.mockResolvedValueOnce({ requires_action: true, payment_authorization: { client_secret: 'secret_1', payment_intent_id: 'pi_1' } });
    mockConfirmPayment.mockResolvedValue({ paymentIntent: { status: 'requires_payment_method' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Authentication needed', 'Card authentication was not completed. Please try again.', 'danger');
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('re-routes to /driver-arriving on a 409 for a searching/driver_assigned/driver_accepted active ride', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'searching' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/driver-arriving', params: { rideId: 'ride-active' } });
  });

  it('re-routes to /ride-in-progress on a 409 for an in-progress active ride', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'in_progress' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-in-progress', params: { rideId: 'ride-active' } });
  });

  it('re-routes to /ride-completed on a 409 for a completed active ride', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'completed' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).toHaveBeenCalledWith({ pathname: '/ride-completed', params: { rideId: 'ride-active' } });
  });

  it('reports a client-side crash to crashlytics when isEngineError classifies the booking failure', async () => {
    mockIsEngineError.mockReturnValueOnce(true);
    mockCreateRide.mockRejectedValue(new TypeError('confirmPayment is not a function'));
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockRecordNonFatal).toHaveBeenCalledWith(
      expect.any(TypeError), { screen: 'payment-confirm', action: 'handleBookRide' },
    );
  });

  it('dismisses the Unpaid Ride confirm sheet via Cancel without navigating', async () => {
    const err: any = new Error('unpaid');
    err.response = { status: 402, data: { error: { details: { unpaid_ride_id: 'ride-old' } } } };
    mockCreateRide.mockRejectedValue(err);
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    const cancelBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel' });
    act(() => { cancelBtn.props.onPress(); });
    expect(allText(r)).not.toContain('Unpaid Ride');
    expect(mockPush).not.toHaveBeenCalled();
  });

  it('navigates back on the header back button', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findByProps({ accessibilityLabel: 'Go back' });
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });

  it('falls back a corporate account with a missing id/name to empty strings, and drops it from the list (no id)', async () => {
    // Also exercises the work-mode default effect (which needs the subtitle
    // to resolve a company_name), proving the id-less first entry was
    // filtered out by `.filter(a => a.id)` rather than crashing or matching.
    mockWorkProfileState = {
      profiles: [{ company: {} }, { company: { id: 'corp-1', name: 'Acme Inc' } }],
      workModeEnabled: true, fetchProfiles: mockFetchWorkProfiles, activeCompanyId: 'corp-1',
    };
    const r = await renderScreen();
    expect(allText(r)).toContain('Acme Inc');
  });

  it('picks the first corporate account as the work-mode default when activeCompanyId is not yet known', async () => {
    mockWorkProfileState = {
      profiles: [{ company: { id: 'corp-1', name: 'Acme Inc' } }],
      workModeEnabled: true, fetchProfiles: mockFetchWorkProfiles, activeCompanyId: null,
    };
    const r = await renderScreen();
    expect(allText(r)).toContain('Acme Inc');
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', 'corp-1', 'card-1');
  });

  it('falls back saved cards to an empty list when the response data is not an array', async () => {
    mockApiGet.mockResolvedValue({ data: null });
    const r = await renderScreen();
    expect(allText(r)).toContain('Tap to add a card');
  });

  it('ignores a rapid double-press on Book (isBooking re-entrancy guard)', async () => {
    let resolveCreate: (v: any) => void;
    mockCreateRide.mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve; }));
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    act(() => { bookBtn.props.onPress(); });
    act(() => { bookBtn.props.onPress(); }); // second tap while the first is still in flight
    await act(async () => {
      resolveCreate!({ id: 'ride-1', status: 'searching' });
      await flush();
    });
    expect(mockCreateRide).toHaveBeenCalledTimes(1);
  });

  it('shows the in-flight spinner instead of the label while booking is in progress', async () => {
    let resolveCreate: (v: any) => void;
    mockCreateRide.mockImplementation(() => new Promise((resolve) => { resolveCreate = resolve; }));
    const r = await renderScreen();
    expect(r.root.findAllByType(ActivityIndicator)).toHaveLength(0);
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    act(() => { bookBtn.props.onPress(); });
    expect(r.root.findAllByType(ActivityIndicator)).toHaveLength(1);
    await act(async () => {
      resolveCreate!({ id: 'ride-1', status: 'searching' });
      await flush();
    });
    expect(r.root.findAllByType(ActivityIndicator)).toHaveLength(0);
  });

  it('disables and dims the Book button while a global isLoading is true', async () => {
    mockRideState.isLoading = true;
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    expect(bookBtn.props.disabled).toBe(true);
    expect(bookBtn.props.accessibilityState.disabled).toBe(true);
  });

  it('books with no card selected via the corporate bypass, sending pmId: undefined', async () => {
    mockApiGet.mockResolvedValue({ data: [] }); // no saved cards -> selectedCardId stays null
    mockWorkProfileState = {
      profiles: [{ company: { id: 'corp-1', name: 'Acme Inc' } }],
      workModeEnabled: true, fetchProfiles: mockFetchWorkProfiles, activeCompanyId: 'corp-1',
    };
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockCreateRide).toHaveBeenCalledWith('card', 'corp-1', undefined);
  });

  it('falls back the SCA error toast to a generic message when confirmError has no .message', async () => {
    mockCreateRide.mockResolvedValueOnce({ requires_action: true, payment_authorization: { client_secret: 'secret_1', payment_intent_id: 'pi_1' } });
    mockConfirmPayment.mockResolvedValue({ error: {} });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Authentication needed', 'Card authentication was not completed.', 'danger');
  });

  it('treats a missing paymentIntent/status after SCA as not-completed authentication', async () => {
    mockCreateRide.mockResolvedValueOnce({ requires_action: true, payment_authorization: { client_secret: 'secret_1', payment_intent_id: 'pi_1' } });
    mockConfirmPayment.mockResolvedValue({ paymentIntent: undefined });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Authentication needed', 'Card authentication was not completed. Please try again.', 'danger');
  });

  it('falls back the analytics vehicle_type to "unknown" when selectedVehicle is null', async () => {
    mockRideState.selectedVehicle = null;
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book undefined' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockAnalyticsRideRequested).toHaveBeenCalledWith(expect.objectContaining({ vehicle_type: 'unknown' }));
  });

  it('falls through to the generic failure toast on a 409 when fetchActiveRide finds no genuinely active ride', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: false, ride: null });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Booking Failed', 'Could not complete your booking. Please try again.', 'danger');
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('navigates nowhere on a 409 for an active ride in an unrecognized status', async () => {
    const err: any = new Error('already active');
    err.response = { status: 409 };
    mockCreateRide.mockRejectedValue(err);
    mockFetchActiveRide.mockResolvedValue({ active: true, ride: { id: 'ride-active', status: 'cancelled' } });
    const r = await renderScreen();
    const bookBtn = r.root.findByProps({ accessibilityLabel: 'Book Sedan' });
    await act(async () => { await bookBtn.props.onPress(); await flush(); });
    expect(mockReplace).not.toHaveBeenCalled();
    expect(mockShowToast).not.toHaveBeenCalledWith('Booking Failed', expect.anything(), expect.anything());
  });

  it('falls back totalFare to total_fare when grand_total is missing, and to 0 when both are missing', async () => {
    mockRideState.estimates = [{ ...ESTIMATE, grand_total: undefined, total_fare: '12.00' }];
    let r = await renderScreen();
    expect(allText(r)).toContain('["$","12.00"]');
    act(() => { r.unmount(); });

    mockRideState.estimates = [{ ...ESTIMATE, grand_total: undefined, total_fare: undefined }];
    r = await renderScreen();
    expect(allText(r)).toContain('["$","0.00"]');
  });

  it('falls back the vehicle-summary price to $0.00 when total_fare is missing', async () => {
    mockRideState.estimates = [{ ...ESTIMATE, total_fare: undefined }];
    const r = await renderScreen();
    // Only the summary price (its own `total_fare || '0'` fallback) renders
    // $0.00 here -- grand_total is untouched, so the footer total (driven by
    // the separate grand_total-first fallback chain) still shows $15.00.
    expect(allText(r)).toContain('["$","0.00"]');
    expect(allText(r)).toContain('["$","15.00"]');
  });

  it('falls back the wallet balance to $0.00 when wallet is null', async () => {
    mockWalletState = { wallet: null, fetchWallet: mockFetchWallet };
    const r = await renderScreen();
    expect(allText(r)).toContain('["Balance: $","0.00"]');
  });

  it('does not crash and renders no fare lines when fare_breakdown is missing', async () => {
    mockRideState.estimates = [{ ...ESTIMATE, fare_breakdown: undefined }];
    const r = await renderScreen();
    const fareHeader = findButtonByText(r, 'View fare details');
    act(() => { fareHeader.props.onPress(); });
    expect(allText(r)).not.toContain('Base fare');
  });

  it('skips a fare-breakdown line whose amount is null', async () => {
    mockRideState.estimates = [{
      ...ESTIMATE,
      fare_breakdown: [
        { label: 'Base fare', amount: '10.00', type: 'ride' },
        { label: 'Pending adjustment', amount: null, type: 'modifier' },
      ],
    }];
    const r = await renderScreen();
    const fareHeader = findButtonByText(r, 'View fare details');
    act(() => { fareHeader.props.onPress(); });
    expect(allText(r)).toContain('Base fare');
    expect(allText(r)).not.toContain('Pending adjustment');
  });

  it('renders a non-ride fare line (e.g. tax) with its plain label, distinct from the ride line', async () => {
    mockRideState.estimates = [{
      ...ESTIMATE,
      fare_breakdown: [
        { label: 'Base fare', amount: '10.00', type: 'ride' },
        { label: 'GST', amount: '0.50', type: 'tax' },
      ],
    }];
    const r = await renderScreen();
    const fareHeader = findButtonByText(r, 'View fare details');
    act(() => { fareHeader.props.onPress(); });
    // The 'ride' line hits the true side of the type==='ride' ternary (its own
    // driver-payout badge); the 'tax' line exercises the false side (plain label).
    expect(allText(r)).toContain('100% goes to your driver');
    expect(allText(r)).toContain('GST');
  });

  it('renders a modifier fare line (e.g. a surcharge) with the modifier color on both the label and its value', async () => {
    mockRideState.estimates = [{
      ...ESTIMATE,
      fare_breakdown: [
        { label: 'Base fare', amount: '10.00', type: 'ride' },
        { label: 'Peak surcharge', amount: '2.00', type: 'modifier' },
      ],
    }];
    const r = await renderScreen();
    const fareHeader = findButtonByText(r, 'View fare details');
    act(() => { fareHeader.props.onPress(); });
    const label = r.root.findAllByType(Text).find((t) => {
      try { return JSON.stringify(t.props.children) === '"Peak surcharge"'; } catch { return false; }
    })!;
    expect(label.props.style).toEqual(expect.arrayContaining([expect.objectContaining({ color: '#EF4444' })]));
  });
});
