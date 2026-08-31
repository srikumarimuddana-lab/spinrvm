/**
 * app/ride-completed.tsx — post-trip rate + tip + pay screen (money-
 * adjacent). Pins:
 *  - fetch-on-mount; auto-dismisses (clears ride, goes home) once, only
 *    if the ride is already paid/waived on first load — never
 *    interferes with the normal pay-then-navigate flow afterward
 *  - hardware back is fully blocked (must complete rating & payment)
 *  - handleSubmit: rates the driver via rateRide exactly once across
 *    retries (hasRatedRef latches); skips the payment attempt entirely
 *    when already paid; on payment success, clears the ride and routes
 *    home; on failure, shows the returned alert and stays on screen
 *  - a payment alert's "Change Card" button pushes to /manage-cards
 *    carrying the ride id, the already-chosen tip, and the rated flag
 *    so a re-render after picking a card doesn't re-rate or drop the tip
 *  - a payment alert's "Retry" button re-invokes handleSubmit with the
 *    same override card
 *  - returning from the Change Card escape (payWithCard param)
 *    auto-retries the charge on that card exactly once
 *  - Google Pay path (Android, card, no pre-auth hold): success rates +
 *    clears + routes home; cancellation is silent, any other failure
 *    toasts
 *  - handleEmailReceipt and Report Lost Item (modal open/submit/cancel)
 *  - a driver photo load error falls back to the placeholder icon
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, TextInput, Image, BackHandler, Platform, Modal, StyleSheet } from 'react-native';
import RNMapView from 'react-native-maps';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));
jest.mock('@shared/components/ErrorBoundary', () => ({
  ErrorBoundary: ({ children }: any) => children,
}));

const mockFitToCoordinates = jest.fn();
jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const MapView = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ fitToCoordinates: (...a: any[]) => mockFitToCoordinates(...a) }));
    return ReactActual.createElement('MapView', props, props.children);
  });
  const Polyline = () => null;
  return { __esModule: true, default: MapView, Polyline, PROVIDER_GOOGLE: 'google' };
});
jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));

const mockPush = jest.fn();
const mockReplace = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', background: '#FFFFFF', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a), post: (...a: any[]) => mockApiPost(...a) },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockGetState = jest.fn(() => ({ user: { email: 'jamie@example.com' } }));
jest.mock('@shared/store/authStore', () => ({ useAuthStore: { getState: () => mockGetState() } }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockAnalyticsPaymentCompleted = jest.fn();
const mockAnalyticsRideCompleted = jest.fn();
jest.mock('@shared/analytics', () => ({
  Analytics: {
    paymentCompleted: (...a: any[]) => mockAnalyticsPaymentCompleted(...a),
    rideCompleted: (...a: any[]) => mockAnalyticsRideCompleted(...a),
  },
}));

const mockConfirmPayment = jest.fn();
jest.mock('@stripe/stripe-react-native', () => ({
  useStripe: () => ({ confirmPayment: (...a: any[]) => mockConfirmPayment(...a) }),
}));

const mockAttemptRidePayment = jest.fn();
jest.mock('../utils/attemptRidePayment', () => ({
  attemptRidePayment: (...a: any[]) => mockAttemptRidePayment(...a),
}));

const mockPresentSheet = jest.fn();
let mockSheetLoading = false;
jest.mock('../hooks/useSpinrPaymentSheet', () => ({
  useSpinrPaymentSheet: () => ({ presentSheet: (...a: any[]) => mockPresentSheet(...a), isLoading: mockSheetLoading }),
}));

const mockOnRideRated = jest.fn();
jest.mock('@shared/utils/appRating', () => ({ onRideRated: (...a: any[]) => mockOnRideRated(...a) }));

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

const mockFetchRide = jest.fn();
const mockRateRide = jest.fn();
const mockClearRide = jest.fn();
let mockRideState: any;
jest.mock('../store/rideStore', () => ({
  useRideStore: Object.assign((...a: any[]) => mockRideState, { getState: () => mockRideState }),
}));

import RideCompletedScreen from '../app/ride-completed';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const CURRENT_RIDE = {
  id: 'ride-1', status: 'completed', ride_code: 'RIDE001',
  pickup_lat: 50.45, pickup_lng: -104.6, dropoff_lat: 50.5, dropoff_lng: -104.5,
  pickup_address: '100 Main St', dropoff_address: '200 Elm St',
  total_fare: '15.00', grand_total: '15.00', distance_km: 5.2, duration_minutes: 12,
  payment_status: 'pending', payment_method: 'card', card_last4: '4242',
};
const CURRENT_DRIVER = { name: 'Sam Lee', rating: 4.9, license_plate: 'ABC 123', photo_url: null };

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<RideCompletedScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => { try { return JSON.stringify(t.props.children); } catch { return '<circular>'; } }).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  Platform.OS = 'ios';
  mockSheetLoading = false;
  mockParams = { rideId: 'ride-1' };
  mockRideState = {
    currentRide: CURRENT_RIDE, currentDriver: CURRENT_DRIVER,
    fetchRide: mockFetchRide, rateRide: mockRateRide, clearRide: mockClearRide,
  };
  mockRateRide.mockResolvedValue(undefined);
  mockAttemptRidePayment.mockResolvedValue({ ok: true, charged: 15 });
  mockOnRideRated.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('RideCompletedScreen', () => {
  it('fetches the ride on mount', async () => {
    await renderScreen();
    expect(mockFetchRide).toHaveBeenCalledWith('ride-1');
  });

  it('auto-dismisses (clears ride, goes home) once, when the ride is already paid on first load', async () => {
    mockRideState.currentRide = { ...CURRENT_RIDE, payment_status: 'paid' };
    await renderScreen();
    expect(mockClearRide).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('fully blocks the hardware back button', async () => {
    const addListenerSpy = jest.spyOn(BackHandler, 'addEventListener');
    await renderScreen();
    const handler = addListenerSpy.mock.calls.find((c) => c[0] === 'hardwareBackPress')![1] as () => boolean;
    expect(handler()).toBe(true);
  });

  it('submits: rates once, pays, clears the ride, and routes home on success', async () => {
    const r = await renderScreen();
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Pay and finish' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockRateRide).toHaveBeenCalledWith('ride-1', 5, undefined, undefined);
    expect(mockAttemptRidePayment).toHaveBeenCalled();
    expect(mockClearRide).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
    expect(mockAnalyticsPaymentCompleted).toHaveBeenCalled();
    expect(mockAnalyticsRideCompleted).toHaveBeenCalled();
  });

  it('skips the payment attempt entirely when already paid, and shows "Rate and finish"', async () => {
    mockRideState.currentRide = { ...CURRENT_RIDE, payment_status: 'paid' === 'paid' ? CURRENT_RIDE.payment_status : CURRENT_RIDE.payment_status };
    // Simulate the "already paid" branch via a payment_status flip AFTER
    // initial mount (the auto-dismiss effect only fires once on first load,
    // so setting alreadyPaid this way exercises the button state, not the
    // dismiss effect).
    const r = await renderScreen();
    await act(async () => {
      mockRideState = { ...mockRideState, currentRide: { ...CURRENT_RIDE, payment_status: 'paid' } };
      renderer!.update(<RideCompletedScreen />);
      await flush();
    });
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Rate and finish' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockAttemptRidePayment).not.toHaveBeenCalled();
    expect(mockClearRide).toHaveBeenCalled();
  });

  it('shows the payment failure alert and stays on screen (no navigation)', async () => {
    mockAttemptRidePayment.mockResolvedValue({
      ok: false,
      alert: {
        title: 'Payment Declined', message: 'Your card was declined.', variant: 'danger',
        buttons: [{ text: 'Change Card', kind: 'change_card' }, { text: 'Cancel', kind: 'cancel' }],
      },
    });
    const r = await renderScreen();
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Pay and finish' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(allText(r)).toContain('Payment Declined');
    expect(mockClearRide).not.toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('"Change Card" pushes to /manage-cards carrying the ride, tip, and rated flag', async () => {
    mockAttemptRidePayment.mockResolvedValue({
      ok: false,
      alert: {
        title: 'Payment Declined', message: 'Your card was declined.', variant: 'danger',
        buttons: [{ text: 'Change Card', kind: 'change_card' }],
      },
    });
    const r = await renderScreen();
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Pay and finish' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    const changeCardBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Change Card' });
    act(() => { changeCardBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith(expect.stringContaining('/manage-cards?rideId=ride-1&forPayment=1&tip=0&rated=1'));
  });

  it('"Retry" re-invokes the submit flow with the same override card', async () => {
    mockAttemptRidePayment
      .mockResolvedValueOnce({ ok: false, alert: { title: 'Payment Declined', message: 'Declined.', variant: 'danger', buttons: [{ text: 'Retry', kind: 'retry' }] } })
      .mockResolvedValueOnce({ ok: true, charged: 15 });
    const r = await renderScreen();
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Pay and finish' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    const retryBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Retry' });
    await act(async () => { await retryBtn.props.onPress(); await flush(); });
    expect(mockAttemptRidePayment).toHaveBeenCalledTimes(2);
    // Only rated once across both attempts.
    expect(mockRateRide).toHaveBeenCalledTimes(1);
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('auto-retries the charge once on the card carried back from the Change Card escape', async () => {
    mockParams = { rideId: 'ride-1', payWithCard: 'card-2', tip: '3', rated: '1' };
    await renderScreen();
    await flush();
    expect(mockAttemptRidePayment).toHaveBeenCalledWith(expect.objectContaining({ paymentMethodId: 'card-2', tipAmount: 3 }));
    // Already rated (rated=1 param) — must not re-rate on the auto-retry.
    expect(mockRateRide).not.toHaveBeenCalled();
  });

  it('Google Pay: success rates, clears, and routes home', async () => {
    Platform.OS = 'android';
    mockRideState.currentRide = { ...CURRENT_RIDE, auth_status: undefined };
    mockPresentSheet.mockResolvedValue({ ok: true });
    const r = await renderScreen();
    const gpayBtn = r.root.findByProps({ accessibilityLabel: 'Pay with Google Pay' });
    await act(async () => { await gpayBtn.props.onPress(); await flush(); });
    expect(mockRateRide).toHaveBeenCalled();
    expect(mockClearRide).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/(tabs)');
  });

  it('Google Pay: cancellation is silent (no toast)', async () => {
    Platform.OS = 'android';
    mockRideState.currentRide = { ...CURRENT_RIDE, auth_status: undefined };
    mockPresentSheet.mockResolvedValue({ ok: false, errorMessage: 'Payment cancelled.' });
    const r = await renderScreen();
    const gpayBtn = r.root.findByProps({ accessibilityLabel: 'Pay with Google Pay' });
    await act(async () => { await gpayBtn.props.onPress(); await flush(); });
    expect(mockShowToast).not.toHaveBeenCalled();
    expect(mockClearRide).not.toHaveBeenCalled();
  });

  it('Google Pay: any other failure toasts', async () => {
    Platform.OS = 'android';
    mockRideState.currentRide = { ...CURRENT_RIDE, auth_status: undefined };
    mockPresentSheet.mockResolvedValue({ ok: false, errorMessage: 'Card declined.' });
    const r = await renderScreen();
    const gpayBtn = r.root.findByProps({ accessibilityLabel: 'Pay with Google Pay' });
    await act(async () => { await gpayBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Payment Failed', 'Card declined.', 'danger');
  });

  it('emails the receipt and toasts', async () => {
    const r = await renderScreen();
    const emailBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Email Receipt"'; } catch { return false; } })
    )!;
    await act(async () => { await emailBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/email-receipt');
    expect(mockShowToast).toHaveBeenCalledWith('Receipt Sent', 'Receipt emailed to jamie@example.com.', 'success');
  });

  it('opens the lost-item modal, submits a report, and toasts', async () => {
    const r = await renderScreen();
    const lostBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Report Lost Item"'; } catch { return false; } })
    )!;
    act(() => { lostBtn.props.onPress(); });
    const input = r.root.findAllByType(TextInput).find((n) => n.props.placeholder === 'e.g. Black backpack with laptop inside')!;
    act(() => { input.props.onChangeText('Black backpack'); });
    const submitReportBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Submit Report"'; } catch { return false; } })
    )!;
    await act(async () => { await submitReportBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/lost-and-found', { item_description: 'Black backpack', item_category: 'other' });
    expect(mockShowToast).toHaveBeenCalledWith('Report Submitted', "Your driver has been notified. They'll get back to you if the item is found.", 'success');
  });

  it('falls back to the placeholder icon when the driver photo fails to load', async () => {
    mockRideState.currentDriver = { ...CURRENT_DRIVER, photo_url: 'https://example.com/x.jpg' };
    const r = await renderScreen();
    const img = r.root.findByType(Image);
    act(() => { img.props.onError(); });
    expect(r.root.findAllByType(Image)).toHaveLength(0);
  });

  it('renders the actual v2 route (with planned underlay + pickup leg) and fits the map on ready', async () => {
    mockRideState.currentRide = {
      ...CURRENT_RIDE,
      route_schema_version: 2,
      route_geometry_status: 'processing', // incomplete -> planned underlay stays on
      planned_route_polyline: [[50.45, -104.6], [50.5, -104.5]],
      actual_route_segments: [
        { id: 'trip-1', coordinates: [[50.46, -104.58], [50.49, -104.52]], geometry_kind: 'observed', phase: 'trip_in_progress' },
        { id: 'pickup-1', coordinates: [[50.44, -104.61], [50.45, -104.6]], geometry_kind: 'observed', phase: 'navigating_to_pickup' },
      ],
    };
    const r = await renderScreen();
    // hasActualRoute -> "Actual route" label rendered.
    expect(allText(r)).toContain('Actual route');
    const mapView = r.root.findByType(RNMapView as any);
    await act(async () => { mapView.props.onMapReady(); await flush(); });
    expect(mockFitToCoordinates).toHaveBeenCalled();
  });

  it('re-fetches the ride when useCompletedRouteRefresh fires its callback', async () => {
    jest.useFakeTimers();
    try {
      mockRideState.currentRide = {
        ...CURRENT_RIDE,
        status: 'completed',
        route_schema_version: 2,
        route_geometry_status: 'pending',
      };
      await renderScreen();
      expect(mockFetchRide).toHaveBeenCalledTimes(1);
      await act(async () => {
        jest.advanceTimersByTime(3000);
        await flush();
      });
      expect(mockFetchRide).toHaveBeenCalledTimes(2);
      expect(mockFetchRide).toHaveBeenLastCalledWith('ride-1');
    } finally {
      jest.useRealTimers();
    }
  });

  it('email receipt failure toasts a danger message', async () => {
    mockApiPost.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    const emailBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Email Receipt"'; } catch { return false; } })
    )!;
    await act(async () => { await emailBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Email Not Sent', 'Could not send receipt email. Please try again.', 'danger');
  });

  it('lost item report submission failure toasts a danger message', async () => {
    mockApiPost.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    const lostBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Report Lost Item"'; } catch { return false; } })
    )!;
    act(() => { lostBtn.props.onPress(); });
    const input = r.root.findAllByType(TextInput).find((n) => n.props.placeholder === 'e.g. Black backpack with laptop inside')!;
    act(() => { input.props.onChangeText('Black backpack'); });
    const submitReportBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Submit Report"'; } catch { return false; } })
    )!;
    await act(async () => { await submitReportBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Report Not Sent', 'Could not submit report. Please try again.', 'danger');
  });

  it('closes the lost-item modal via Cancel and clears the draft text', async () => {
    const r = await renderScreen();
    const lostBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Report Lost Item"'; } catch { return false; } })
    )!;
    act(() => { lostBtn.props.onPress(); });
    const input = r.root.findAllByType(TextInput).find((n) => n.props.placeholder === 'e.g. Black backpack with laptop inside')!;
    act(() => { input.props.onChangeText('Black backpack'); });
    const cancelBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Cancel"'; } catch { return false; } })
    )!;
    act(() => { cancelBtn.props.onPress(); });
    const modal = r.root.findByType(Modal);
    expect(modal.props.visible).toBe(false);
  });

  it('dismisses the lost-item modal via onRequestClose', async () => {
    const r = await renderScreen();
    const lostBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Report Lost Item"'; } catch { return false; } })
    )!;
    act(() => { lostBtn.props.onPress(); });
    let modal = r.root.findByType(Modal);
    expect(modal.props.visible).toBe(true);
    act(() => { modal.props.onRequestClose(); });
    modal = r.root.findByType(Modal);
    expect(modal.props.visible).toBe(false);
  });

  it('"Support" (payment alert) pushes to /support with the ride + payment_failed topic', async () => {
    mockAttemptRidePayment.mockResolvedValue({
      ok: false,
      alert: {
        title: 'Payment Failed', message: 'Something went wrong.', variant: 'danger',
        buttons: [{ text: 'Contact Support', kind: 'support' }],
      },
    });
    const r = await renderScreen();
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Pay and finish' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    const supportBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Contact Support' });
    act(() => { supportBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/support?rideId=ride-1&topic=payment_failed');
  });

  it('a payment alert "Cancel" button (no onPress) closes via ConfirmSheet onClose', async () => {
    mockAttemptRidePayment.mockResolvedValue({
      ok: false,
      alert: {
        title: 'Payment Declined', message: 'Your card was declined.', variant: 'danger',
        buttons: [{ text: 'Cancel', kind: 'cancel' }],
      },
    });
    const r = await renderScreen();
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Pay and finish' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(allText(r)).toContain('Payment Declined');
    const cancelBtn = r.root.findByProps({ accessibilityLabel: 'confirm-Cancel' });
    act(() => { cancelBtn.props.onPress(); });
    expect(allText(r)).not.toContain('Payment Declined');
  });

  it('handleSubmit surfaces an unexpected error (e.g. attemptRidePayment throwing) as a toast, not a crash', async () => {
    mockAttemptRidePayment.mockRejectedValue(new Error('boom'));
    const r = await renderScreen();
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Pay and finish' });
    await act(async () => { await submitBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Submit Failed', 'Failed to submit. Please try again.', 'danger');
    expect(mockClearRide).not.toHaveBeenCalled();
  });

  it('tapping a star sets the rating and its descriptive copy', async () => {
    const r = await renderScreen();
    const star2 = r.root.findByProps({ accessibilityLabel: 'Rate 2 stars' });
    act(() => { star2.props.onPress(); });
    expect(allText(r)).toContain('Could be better');
  });

  it('tapping a tip preset selects it, and tapping again deselects it', async () => {
    const r = await renderScreen();
    // fare 15 -> ladder [2, 3, 4]
    const tipBtn = r.root.findByProps({ accessibilityLabel: 'Tip $3' });
    act(() => { tipBtn.props.onPress(); });
    expect(r.root.findByProps({ accessibilityLabel: 'Tip $3' }).props.accessibilityState.checked).toBe(true);
    act(() => { tipBtn.props.onPress(); });
    expect(r.root.findByProps({ accessibilityLabel: 'Tip $3' }).props.accessibilityState.checked).toBe(false);
  });

  it('typing a custom tip clears any selected preset and is reflected in the Pay button total', async () => {
    const r = await renderScreen();
    const customInput = r.root.findAllByType(TextInput).find((n) => n.props.placeholder === 'Other')!;
    act(() => { customInput.props.onChangeText('7'); });
    const submitBtn = r.root.findByProps({ accessibilityLabel: 'Pay and finish' });
    expect(JSON.stringify(submitBtn.findAllByType(Text)[0].props.children)).toContain('22.00');
  });

  // The custom-tip box fills itself with colors.text as soon as it holds a
  // value (styles.tipCustomActive). Its contents therefore have to flip to the
  // contrasting colors.background, or the rider types a tip they cannot read.
  const customTipParts = (r: TestRenderer.ReactTestRenderer) => ({
    input: r.root.findAllByType(TextInput).find((n) => n.props.placeholder === 'Other')!,
    // Only the standalone prefix has a bare '$' string child — the preset
    // pills and the fare total render arrays like ['$', 3].
    dollar: r.root.findAllByType(Text).find((t) => t.props.children === '$')!,
  });

  it('flips the custom tip text to the contrasting colour once the box is filled', async () => {
    const r = await renderScreen();
    act(() => { customTipParts(r).input.props.onChangeText('7'); });

    const { input, dollar } = customTipParts(r);
    expect(StyleSheet.flatten(input.props.style).color).toBe(COLORS.background);
    expect(input.props.selectionColor).toBe(COLORS.background);
    expect(StyleSheet.flatten(dollar.props.style).color).toBe(COLORS.background);
  });

  it('keeps the empty custom tip box on the plain surface palette', async () => {
    const r = await renderScreen();
    const { input, dollar } = customTipParts(r);
    expect(StyleSheet.flatten(input.props.style).color).toBe(COLORS.text);
    expect(input.props.placeholderTextColor).toBe(COLORS.textDim);
    expect(StyleSheet.flatten(dollar.props.style).color).toBe(COLORS.textDim);
  });

  it('pressing "Message Driver" navigates to the driver chat for this ride', async () => {
    const r = await renderScreen();
    const msgBtn = r.root.findAllByType(TouchableOpacity).find((n) =>
      n.findAllByType(Text).some((t) => { try { return JSON.stringify(t.props.children) === '"Message Driver"'; } catch { return false; } })
    )!;
    act(() => { msgBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/chat-driver?rideId=ride-1');
  });

  it('shows the wallet payment badge for wallet-paid rides', async () => {
    mockRideState.currentRide = { ...CURRENT_RIDE, payment_method: 'wallet' };
    const r = await renderScreen();
    expect(allText(r)).toContain('Spinr Wallet');
  });

  it('shows the company-account payment badge for corporate-allowance rides', async () => {
    mockRideState.currentRide = { ...CURRENT_RIDE, payment_method: 'company_allowance' };
    const r = await renderScreen();
    expect(allText(r)).toContain('Company Account');
  });

  it('falls back to a plain "Card" badge when there is no card_last4', async () => {
    mockRideState.currentRide = { ...CURRENT_RIDE, payment_method: 'card', card_last4: undefined };
    const r = await renderScreen();
    expect(allText(r)).toContain('"Card"');
  });

  it('shows "0 km/h" avg speed when duration is 0', async () => {
    mockRideState.currentRide = { ...CURRENT_RIDE, duration_minutes: 0, actual_duration_minutes: undefined };
    const r = await renderScreen();
    expect(allText(r)).toContain('["0"," km/h"]');
  });

  it('shows the pre-auth hold hint and hides Google Pay when the ride has a hold', async () => {
    Platform.OS = 'android';
    mockRideState.currentRide = { ...CURRENT_RIDE, auth_status: 'authorized' };
    const r = await renderScreen();
    expect(allText(r)).toContain('Charged to the card you chose at booking. Any tip is included in the same charge.');
    expect(() => r.root.findByProps({ accessibilityLabel: 'Pay with Google Pay' })).toThrow();
  });
});
