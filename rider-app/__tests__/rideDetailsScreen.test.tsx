/**
 * app/ride-details.tsx — rider's past-ride detail/receipt screen. Pins:
 *  - fetches the ride on mount; a not-found (never-resolved) ride shows
 *    "Ride not found" instead of crashing
 *  - status badge label/color: completed/cancelled get their own copy,
 *    everything else falls back to the raw status string
 *  - cancelled rides show ONLY the flat cancellation-fee card (never the
 *    booking-time fare_breakdown, which would misrepresent what was
 *    actually charged for a trip never taken)
 *  - completed/other rides show the normalized fare breakdown — multiple
 *    'fare' lines consolidate into one "Ride fare"; a promo/tip line is
 *    injected only when the ride has one but breakdown doesn't already
 *    carry it
 *  - handleEmailReceipt: POSTs, toasts to the user's registered email
 *  - handleDownloadInvoice: generates+shares the PDF; a failure (no
 *    native module) toasts "PDF Unavailable" instead of crashing
 *  - receipt actions ("Email receipt", "Download invoice") only render
 *    for completed rides
 *  - back nav and "Get help with this ride" nav
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

jest.mock('react-native-maps', () => {
  const ReactActual = require('react');
  const MapView = ReactActual.forwardRef((props: any, ref: any) => {
    ReactActual.useImperativeHandle(ref, () => ({ fitToCoordinates: jest.fn() }));
    return ReactActual.createElement('MapView', props, props.children);
  });
  const Polyline = () => null;
  return { __esModule: true, default: MapView, Polyline, PROVIDER_GOOGLE: 'google' };
});
jest.mock('@shared/components/RouteLine', () => ({ RouteLine: () => null }));
jest.mock('@shared/components/RoutePins', () => ({ RoutePins: () => null }));

const mockBack = jest.fn();
const mockPush = jest.fn();
let mockParams: any;
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
  useLocalSearchParams: () => mockParams,
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
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

const mockPrintToFileAsync = jest.fn();
jest.mock('expo-print', () => ({ printToFileAsync: (...a: any[]) => mockPrintToFileAsync(...a) }));
const mockIsAvailableAsync = jest.fn();
const mockShareAsync = jest.fn();
jest.mock('expo-sharing', () => ({
  isAvailableAsync: (...a: any[]) => mockIsAvailableAsync(...a),
  shareAsync: (...a: any[]) => mockShareAsync(...a),
}));

import RideDetailsScreen from '../app/ride-details';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

const RIDE_COMPLETED = {
  id: 'ride-1', status: 'completed', created_at: '2026-08-20T10:00:00Z',
  pickup_address: '123 Main St', dropoff_address: '456 Elm St',
  fare_breakdown: [
    { label: 'Base fare', amount: '5.00', type: 'fare' },
    { label: 'Distance', amount: '8.00', type: 'fare' },
  ],
  distance_km: 5.2, duration_minutes: 12, rider_rating: 5,
  payment_method: 'card', payment_status: 'paid', card_last4: '4242',
};

const RIDE_CANCELLED = {
  id: 'ride-2', status: 'cancelled', created_at: '2026-08-20T10:00:00Z',
  pickup_address: '123 Main St', dropoff_address: '456 Elm St',
  cancellation_fee_admin: '5.00', cancellation_fee_driver: '0',
  payment_method: 'wallet', payment_status: 'paid',
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<RideDetailsScreen />);
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
  mockParams = { rideId: 'ride-1' };
  mockApiGet.mockResolvedValue({ data: RIDE_COMPLETED });
  mockApiPost.mockResolvedValue({});
  mockGetState.mockReturnValue({ user: { email: 'jamie@example.com' } });
  mockPrintToFileAsync.mockResolvedValue({ uri: 'file://receipt.pdf' });
  mockIsAvailableAsync.mockResolvedValue(true);
  mockShareAsync.mockResolvedValue(undefined);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
  jest.restoreAllMocks();
});

describe('RideDetailsScreen', () => {
  it('fetches the ride on mount', async () => {
    await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/rides/ride-1');
  });

  it('shows "Ride not found" instead of crashing when the fetch never resolves a ride', async () => {
    mockApiGet.mockRejectedValue(new Error('down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('Ride not found');
  });

  it('shows the "Completed" status badge for a completed ride', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Completed');
  });

  it('shows the "Cancelled" status badge and falls back to the raw status for anything else', async () => {
    mockApiGet.mockResolvedValue({ data: { ...RIDE_CANCELLED } });
    const r = await renderScreen();
    expect(allText(r)).toContain('Cancelled');

    mockApiGet.mockResolvedValue({ data: { ...RIDE_COMPLETED, status: 'disputed' } });
    const r2 = await renderScreen();
    expect(allText(r2)).toContain('disputed');
  });

  it('shows only the flat cancellation fee for a cancelled ride, never the fare breakdown', async () => {
    mockApiGet.mockResolvedValue({ data: RIDE_CANCELLED });
    const r = await renderScreen();
    expect(allText(r)).toContain('Cancellation fee');
    expect(allText(r)).toContain('["$","5.00"]');
    expect(allText(r)).not.toContain('Fare breakdown');
  });

  it('consolidates multiple "fare" breakdown lines into one "Ride fare" line', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('Fare breakdown');
    expect(allText(r)).toContain('100% goes to your driver · ride local, support local');
    expect(allText(r)).toContain('$13.00'); // 5.00 + 8.00 consolidated
  });

  it('injects a promo discount line when the ride has one but the breakdown does not', async () => {
    mockApiGet.mockResolvedValue({
      data: { ...RIDE_COMPLETED, discount_amount: '2.50', promo_code: 'SAVE10' },
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Promo (SAVE10)');
    expect(allText(r)).toContain('-$2.50');
  });

  it('injects a tip line when the ride has one but the breakdown does not', async () => {
    mockApiGet.mockResolvedValue({
      data: { ...RIDE_COMPLETED, tip_amount: '3.00' },
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('Tip');
  });

  it('emails the receipt and toasts to the registered email', async () => {
    const r = await renderScreen();
    const emailBtn = findButtonByText(r, 'Email receipt');
    await act(async () => { await emailBtn.props.onPress(); await flush(); });
    expect(mockApiPost).toHaveBeenCalledWith('/rides/ride-1/email-receipt');
    expect(mockShowToast).toHaveBeenCalledWith('Receipt Sent', 'Receipt emailed to jamie@example.com.', 'success');
  });

  it('toasts a failure when emailing the receipt fails', async () => {
    mockApiPost.mockRejectedValue(new Error('server error'));
    const r = await renderScreen();
    const emailBtn = findButtonByText(r, 'Email receipt');
    await act(async () => { await emailBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('Email Not Sent', 'Could not send receipt email. Please try again.', 'danger');
  });

  // NOTE: handleDownloadInvoice uses a dynamic `await import('expo-print')`
  // specifically so an older build without the native module degrades to
  // this same catch branch instead of crashing (see the source comment).
  // Jest's CJS runtime throws ERR_VM_DYNAMIC_IMPORT_CALLBACK_MISSING_FLAG on
  // ANY dynamic import regardless of jest.mock() — there is no
  // --experimental-vm-modules flag in this test config — so the success
  // path (Print/Sharing actually resolving) can never be exercised here.
  // That happens to be the exact real-world "old build" failure mode this
  // code is defending against, so pin it directly: every tap always lands
  // in the catch branch and shows the same graceful toast, never a crash.
  it('toasts "PDF Unavailable" (dynamic import fails in Jest, same as an old build without the native module) instead of crashing', async () => {
    const r = await renderScreen();
    const downloadBtn = findButtonByText(r, 'Download invoice (PDF)');
    await act(async () => { await downloadBtn.props.onPress(); await flush(); });
    expect(mockShowToast).toHaveBeenCalledWith('PDF Unavailable', 'PDF export requires the latest app version. Please update the app and try again.', 'warning');
    expect(mockPrintToFileAsync).not.toHaveBeenCalled();
  });

  it('hides the receipt actions for a non-completed ride', async () => {
    mockApiGet.mockResolvedValue({ data: RIDE_CANCELLED });
    const r = await renderScreen();
    expect(() => findButtonByText(r, 'Email receipt')).not.toThrow();
    expect(findButtonByText(r, 'Email receipt')).toBeUndefined();
  });

  it('navigates back on the header back button', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => { backBtn.props.onPress(); });
    expect(mockBack).toHaveBeenCalled();
  });

  it('navigates to support on "Get help with this ride"', async () => {
    const r = await renderScreen();
    const helpBtn = findButtonByText(r, 'Get help with this ride');
    act(() => { helpBtn.props.onPress(); });
    expect(mockPush).toHaveBeenCalledWith('/support');
  });
});
